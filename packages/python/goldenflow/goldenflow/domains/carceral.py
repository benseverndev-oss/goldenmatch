"""Carceral (U.S. prisons, jails, detention centers) domain pack.

Targets the joining of HIFLD Prison Boundaries, EPA ECHO, state DOC
inventories, and SDWA registrations — datasets that share physical
facilities but disagree on naming convention. The three carceral-specific
problems this pack solves are:

1. **Operator-org prefixes.** ECHO ships records like
   ``"MDOC, SOUTH MISSISSIPPI CORRECTIONAL INSTITUTION"`` while HIFLD
   says ``"SOUTH MISSISSIPPI CORRECTIONAL INSTITUTION"``. Carceral
   records reliably prefix the operating agency (state DOC, private
   operator) followed by ``,`` / ``-`` / ``:`` / ``/``. Stripping the
   prefix before any fuzzy comparison is the single biggest precision
   win on this domain.

2. **Federal facility-type abbreviations.** ``USP``, ``FCI``, ``FCC``,
   ``FPC``, ``FMC``, ``FDC``, ``ADC`` — Bureau of Prisons abbreviations
   that vary across HIFLD (short form) and ECHO (long form). The
   ``carceral_abbreviate`` transform expands them.

3. **State-prison-complex aliases.** Arizona's HIFLD names start with
   ``ASPC-`` while ECHO files them as ``ASP -`` / ``APS-`` (typo).
   Without aliasing, Jaro-Winkler on those names drops below 0.65;
   with aliasing, real positives clear 0.97. The same HIFLD-vs-ECHO
   abbreviation split holds for PA (``SCI`` vs ``STATE CORRECTIONAL
   INSTITUTION``) and CA (``CSP``/``CCI``/``CIM``/``CIW``/``CTF`` vs
   their CDCR long forms), which are aliased here too. TX (TDCJ) units
   have no analogous facility abbreviation — they're ``<name> Unit`` on
   both sides — so the TX win comes from the operator-prefix stripper
   recognizing the spelled-out ``TEXAS DEPARTMENT OF CRIMINAL JUSTICE``
   (see ``_OPERATOR_PHRASE_RE``), not from an alias.

The pack does **not** redefine address / ZIP / state / unit
normalization — it composes the existing ``address_standardize``,
``zip_normalize``, ``state_abbreviate``, and ``unit_normalize``
transforms in its ``default_config``.
"""

from __future__ import annotations

import re

from goldenflow._polars_lazy import pl
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
from goldenflow.domains.base import DomainPack
from goldenflow.transforms import register_transform

# ── Carceral-domain constants (public, so users can extend) ─────────────

#: Single-token operator-org acronyms used by state DOCs and private
#: corrections operators. Stripped only when they appear as a leading
#: prefix followed by a separator (``,`` / ``-`` / ``:`` / ``/``); mid-string
#: occurrences are left alone so a facility actually *named* "DOC" — rare
#: but possible — doesn't lose discriminating signal.
CARCERAL_OPERATOR_ORGS: frozenset[str] = frozenset({
    "MDOC", "TDCJ", "CDCR", "FDOC", "GDC", "IDOC", "NCDPS",
    "DOC", "DOCR", "DOCS",
    "CCA", "CORECIVIC", "GEO GROUP", "GEO",
})

#: Phrase-form operator-org prefixes. ECHO uses these long-form variants
#: alongside the acronyms above: ``"TX DEPT OF CRIM JUST- MCCONNELL UNIT"``,
#: ``"PA DEPT OF CORR/CHESTER SCI"``.
_OPERATOR_PHRASE_RE = re.compile(
    r"^(?:"
    r"(?:[A-Z]{2}|TEXAS|CALIFORNIA|FLORIDA|MISSISSIPPI|GEORGIA|INDIANA)"
    # DEP / DEPT / DEPARTMENT — ECHO spells the agency out in full
    # ("TEXAS DEPARTMENT OF CRIMINAL JUSTICE - ALLRED UNIT") while HIFLD
    # ships just the unit name; both must strip to the same stem.
    r"\s+DEP(?:T|ARTMENT)?\s+OF\s+(?:CORR(?:ECTIONS?)?|CRIM(?:INAL)?\s+JUST(?:ICE)?)"
    r")\s*[,\-:/]\s*"
)

_OPERATOR_ACRONYM_RE = re.compile(
    r"^(?:" + "|".join(sorted(CARCERAL_OPERATOR_ORGS, key=len, reverse=True)) + r")\s*[,\-:/]\s+"
)

#: Federal Bureau of Prisons facility-type abbreviations. Expanded by
#: ``carceral_abbreviate`` so HIFLD's ``"USP HAZELTON"`` and ECHO's
#: ``"UNITED STATES PENITENTIARY HAZELTON"`` land in the same shape.
CARCERAL_BOP_ABBREVIATIONS: dict[str, str] = {
    "USP": "UNITED STATES PENITENTIARY",
    "FCI": "FEDERAL CORRECTIONAL INSTITUTION",
    "FCC": "FEDERAL CORRECTIONAL COMPLEX",
    "FPC": "FEDERAL PRISON CAMP",
    "FMC": "FEDERAL MEDICAL CENTER",
    "FDC": "FEDERAL DETENTION CENTER",
    "ADC": "ADMINISTRATIVE DETENTION CENTER",
}

#: State-prison-complex name aliases. HIFLD uses one form; ECHO often
#: uses a different one (sometimes a typo). Both sides are mapped to the
#: long form so the name scorer sees a common prefix. Add more here as
#: per-state patterns are discovered.
#:
#: Note these are word-bounded global expansions, not state-scoped — a
#: token that means different things in two states (e.g. CA's Avenal
#: State Prison is also "ASP") will be expanded to the listed long form
#: regardless of state. That can mislabel a name but never fuses two
#: *different* facilities: if both sides genuinely refer to the same
#: place they expand identically; if they don't, they still differ.
CARCERAL_STATE_COMPLEX_ALIASES: dict[str, str] = {
    # Arizona (HIFLD "ASPC-" vs ECHO "ASP -" / "APS-" typo)
    "ASPC": "ARIZONA STATE PRISON COMPLEX",
    "ASP": "ARIZONA STATE PRISON",
    "APS": "ARIZONA STATE PRISON",  # observed ECHO typo for "ASP"
    # Pennsylvania (HIFLD "SCI <name>" vs ECHO "STATE CORRECTIONAL
    # INSTITUTION <name>")
    "SCI": "STATE CORRECTIONAL INSTITUTION",
    # California CDCR facility-type abbreviations (HIFLD short vs ECHO
    # long form). Multi-letter, so word-bounded expansion is safe.
    "CSP": "CALIFORNIA STATE PRISON",
    "CCI": "CALIFORNIA CORRECTIONAL INSTITUTION",
    "CIM": "CALIFORNIA INSTITUTION FOR MEN",
    "CIW": "CALIFORNIA INSTITUTION FOR WOMEN",
    "CTF": "CORRECTIONAL TRAINING FACILITY",
}

_OPERATOR_SUFFIX_RE = re.compile(r"\b(LLC|INC|CORP|CO|LTD)\b\.?\s*$")
_NON_ALNUM_RE = re.compile(r"[^A-Z0-9 ]+")
_WHITESPACE_RE = re.compile(r"\s+")


# ── Transforms ─────────────────────────────────────────────────────────


def _strip_operator_prefix(s: str) -> str:
    """Drop a leading operator-org phrase or acronym + separator."""
    s = _OPERATOR_PHRASE_RE.sub("", s)
    s = _OPERATOR_ACRONYM_RE.sub("", s)
    return s


def _expand_abbreviations(s: str) -> str:
    for short, long in CARCERAL_BOP_ABBREVIATIONS.items():
        s = re.sub(rf"\b{short}\b", long, s)
    for short, long in CARCERAL_STATE_COMPLEX_ALIASES.items():
        s = re.sub(rf"\b{short}\b", long, s)
    return s


@register_transform(
    name="carceral_org_strip",
    input_types=["string"],
    auto_apply=False,
    priority=55,
    mode="series",
)
def carceral_org_strip(series: pl.Series) -> pl.Series:
    """Strip leading operator-org prefix from a carceral facility name.

    Examples:
        ``"MDOC, SOUTH MISS CORRECTIONAL INSTITUTION"`` -> ``"SOUTH MISS CORRECTIONAL INSTITUTION"``
        ``"TX DEPT OF CRIM JUST- MCCONNELL UNIT"`` -> ``"MCCONNELL UNIT"``
        ``"PA DEPT OF CORR/CHESTER SCI"`` -> ``"CHESTER SCI"``
    """

    def _strip(val: str | None) -> str | None:
        if val is None:
            return None
        return _strip_operator_prefix(val.upper().strip()).strip()

    return series.map_elements(_strip, return_dtype=pl.Utf8)


@register_transform(
    name="carceral_abbreviate",
    input_types=["string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def carceral_abbreviate(series: pl.Series) -> pl.Series:
    """Expand carceral facility-type abbreviations and state-complex aliases.

    Covers the BOP set (``USP``, ``FCI``, ``FCC``, ``FPC``, ``FMC``, ``FDC``,
    ``ADC``) plus the Arizona ``ASPC`` / ``ASP`` / ``APS`` alias cluster.
    Operates word-bounded; mid-token occurrences are left alone.
    """

    def _expand(val: str | None) -> str | None:
        if val is None:
            return None
        return _expand_abbreviations(val.upper().strip())

    return series.map_elements(_expand, return_dtype=pl.Utf8)


@register_transform(
    name="carceral_name_normalize",
    input_types=["string"],
    auto_apply=False,
    priority=60,
    mode="series",
)
def carceral_name_normalize(series: pl.Series) -> pl.Series:
    """Full carceral name pipeline: org-strip + uppercase + punctuation strip
    + abbreviation expand + legal-suffix strip. The output is suitable for
    Jaro-Winkler / token-set scoring against another normalized name."""

    def _norm(val: str | None) -> str | None:
        if val is None:
            return None
        s = val.upper().strip()
        s = _strip_operator_prefix(s)
        s = _NON_ALNUM_RE.sub(" ", s)
        s = _WHITESPACE_RE.sub(" ", s).strip()
        s = _expand_abbreviations(s)
        s = _OPERATOR_SUFFIX_RE.sub("", s).strip()
        s = _WHITESPACE_RE.sub(" ", s).strip()
        return s

    return series.map_elements(_norm, return_dtype=pl.Utf8)


@register_transform(
    name="latlng_pack",
    input_types=["string"],
    auto_apply=False,
    priority=40,
    mode="dataframe",
)
def latlng_pack(df: pl.DataFrame) -> pl.DataFrame:
    """Pack ``lat`` + ``lng`` into a single ``latlng`` column shaped
    ``"<lat>|<lng>"`` (empty when either is null).

    Not carceral-specific — useful for any geo-aware matchkey where a
    custom scorer plugin (e.g. ``haversine_decay``) needs both coordinates
    via the single-value ``ScorerPlugin.score_pair`` protocol. Lives in
    this pack until a more general geo module is added.

    Idempotent: skips silently if ``lat`` or ``lng`` is missing.
    """
    if "lat" not in df.columns or "lng" not in df.columns:
        return df
    return df.with_columns(
        pl.when(pl.col("lat").is_null() | pl.col("lng").is_null())
        .then(pl.lit(""))
        .otherwise(pl.col("lat").cast(pl.Utf8) + pl.lit("|") + pl.col("lng").cast(pl.Utf8))
        .alias("latlng")
    )


PACK = DomainPack(
    name="carceral",
    description=(
        "U.S. carceral facilities (prisons, jails, detention centers). "
        "Operator-org prefix stripping (state DOCs + private operators), "
        "BOP facility-type abbreviation expansion (USP/FCI/FCC/...), "
        "state-prison-complex aliasing (Arizona ASPC/ASP/APS), and "
        "lat/lng packing for geo-aware scorers. Composes with the existing "
        "address_standardize / zip_normalize / state_abbreviate transforms."
    ),
    transforms=[
        "carceral_org_strip",
        "carceral_abbreviate",
        "carceral_name_normalize",
        "latlng_pack",
        # composed-from-existing-pack:
        "address_standardize",
        "unit_normalize",
        "zip_normalize",
        "state_abbreviate",
    ],
    default_config=GoldenFlowConfig(
        transforms=[
            TransformSpec(column="name", ops=["carceral_name_normalize"]),
            TransformSpec(column="address", ops=["strip", "address_standardize", "unit_normalize"]),
            TransformSpec(column="city", ops=["strip", "upper"]),
            TransformSpec(column="state", ops=["state_abbreviate"]),
            TransformSpec(column="zip", ops=["zip_normalize"]),
        ]
    ),
)
