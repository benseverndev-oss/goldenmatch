"""Discriminative-power veto for exact/identity matchkeys (#1351).

Cardinality cannot separate a shared identity key (``npi``: records sharing a
value are the SAME entity) from a shared locality attribute (``zip``: records
sharing a value are DIFFERENT people in one area) -- both have moderate
cardinality. This module measures, from the data, whether records that SHARE a
candidate value also AGREE on other identity fields. Low co-agreement => the
value is a locality/attribute, not an identity key => veto its standalone exact
matchkey (demote to blocking-only). Veto-only; never promotes.
"""
from __future__ import annotations

import os
from difflib import SequenceMatcher
from typing import Any

import polars as pl

_IDENTITY_BASKET_TYPES = frozenset({"name", "multi_name", "email", "phone", "identifier"})
# Basket types compared FUZZILY (a true duplicate's name is often corrupted --
# "Smith"/"Smyth" -- so exact-equality would read a genuine identity key's
# shared-value pairs as disagreement and wrongly veto it, e.g. soc_sec_id on
# febrl3). Structured ids (email/phone/identifier) are compared exactly: a
# near-miss there means a DIFFERENT entity, not a corruption of the same one.
_NAME_FUZZY_TYPES = frozenset({"name", "multi_name"})

_TAU_DEFAULT = 0.5
_MIN_SHARED_PAIRS = 20
_MAX_PAIRS = 200
# SequenceMatcher ratio at/above which two name strings count as agreement.
_AGREE_THRESHOLD = 0.85


def veto_enabled() -> bool:
    """Kill-switch: GOLDENMATCH_DISCRIMINATIVE_VETO=0 disables the veto."""
    return os.environ.get("GOLDENMATCH_DISCRIMINATIVE_VETO", "1") != "0"


def tau() -> float:
    """Co-agreement floor below which an exact key is vetoed (env-overridable)."""
    raw = os.environ.get("GOLDENMATCH_DISCRIMINATIVE_TAU")
    if raw is None:
        return _TAU_DEFAULT
    try:
        val = float(raw)
    except (ValueError, TypeError):
        return _TAU_DEFAULT
    return val if 0.0 <= val <= 1.0 else _TAU_DEFAULT


def identity_basket(candidate_col: str, profiles: list[Any]) -> list[tuple[str, bool]]:
    """Other identity-typed columns as ``(name, fuzzy)`` pairs (excludes candidate).

    ``fuzzy`` is True for name-typed columns (compared with a similarity
    threshold to tolerate duplicate-record corruption), False for structured
    identity types (email/phone/identifier, compared exactly).
    """
    out: list[tuple[str, bool]] = []
    for p in profiles:
        col_type = getattr(p, "col_type", None)
        if p.name != candidate_col and col_type in _IDENTITY_BASKET_TYPES:
            out.append((p.name, col_type in _NAME_FUZZY_TYPES))
    return out


def _norm(v: Any) -> str | None:
    """Normalize a cell for equality: str -> stripped lower; blank/None -> None."""
    if v is None:
        return None
    s = str(v).strip().lower()
    return s or None


def _agree(a: str, b: str, fuzzy: bool) -> bool:
    """Whether two normalized cells agree. Exact match always counts; for fuzzy
    (name) fields, a SequenceMatcher ratio >= _AGREE_THRESHOLD also counts."""
    if a == b:
        return True
    if not fuzzy:
        return False
    return SequenceMatcher(None, a, b).ratio() >= _AGREE_THRESHOLD


def discriminative_power(
    df: pl.DataFrame,
    candidate_col: str,
    basket: list[tuple[str, bool]],
    *,
    max_pairs: int = _MAX_PAIRS,
) -> tuple[float, int]:
    """Mean co-agreement over shared-value pairs, and support (n pairs measured).

    ``basket`` is a list of ``(column, fuzzy)`` pairs. Groups df by
    candidate_col; for value-groups with >=2 rows, forms up to max_pairs
    record-pairs deterministically (row 0 paired with rows 1..k-1 within each
    group, groups visited in sorted-value order). Per pair, agreement =
    (# basket fields where BOTH cells are non-null and :func:`_agree`) /
    (# basket fields where BOTH are non-null); name-typed fields agree fuzzily,
    structured ids exactly. Pairs with no jointly-populated basket field are
    skipped. Returns (mean over measured pairs, count of measured pairs);
    (0.0, 0) when basket empty, candidate absent, or no measurable pair exists.
    """
    if not basket or candidate_col not in df.columns:
        return 0.0, 0
    basket_cols = [(c, fuzzy) for (c, fuzzy) in basket if c in df.columns]
    if not basket_cols:
        return 0.0, 0
    sub = df.select([candidate_col, *[c for (c, _f) in basket_cols]])

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in sub.iter_rows(named=True):
        cv = _norm(row[candidate_col])
        if cv is None:
            continue
        groups.setdefault(cv, []).append(row)

    total = 0.0
    measured = 0
    for cv in sorted(groups):
        rows = groups[cv]
        if len(rows) < 2 or measured >= max_pairs:
            continue
        anchor = rows[0]
        for other in rows[1:]:
            if measured >= max_pairs:
                break
            agree = 0
            comparable = 0
            for c, fuzzy in basket_cols:
                a, b = _norm(anchor[c]), _norm(other[c])
                if a is None or b is None:
                    continue
                comparable += 1
                if _agree(a, b, fuzzy):
                    agree += 1
            if comparable == 0:
                continue
            total += agree / comparable
            measured += 1
    if measured == 0:
        return 0.0, 0
    return total / measured, measured


def should_veto_exact(
    df: pl.DataFrame | None,
    candidate_col: str,
    profiles: list[Any],
    *,
    min_shared_pairs: int = _MIN_SHARED_PAIRS,
    max_pairs: int = _MAX_PAIRS,
) -> bool:
    """True => demote the proposed standalone exact matchkey on candidate_col.

    Fail-safe = keep (False) on: kill-switch off, df is None, empty identity
    basket, or insufficient shared-value support. Only vetoes a high-density
    column whose shared-value pairs measurably fail to co-agree on other identity
    fields (support >= min_shared_pairs AND power < tau()).
    """
    if not veto_enabled() or df is None:
        return False
    basket = identity_basket(candidate_col, profiles)
    if not basket:
        return False
    power, support = discriminative_power(df, candidate_col, basket, max_pairs=max_pairs)
    if support < min_shared_pairs:
        return False
    return power < tau()


# ---------------------------------------------------------------------------
# Weighted-fuzzy ATTRIBUTE demotion (single-source workplace/locality attributes)
# ---------------------------------------------------------------------------

# Col_types that are a WORKPLACE / LOCALITY signal rather than a person identity
# claim, and so are candidates for the discriminative-power demotion (of either
# an exact OR a weighted-fuzzy use). ``phone`` is here because a shared clinic /
# switchboard line collapses colleagues under an exact_phone matchkey -- the
# dominant DERM over-merge -- yet a personal cell is a strong identity signal;
# the name-anchored measure keeps the personal cell (its shared-value records DO
# co-agree on name) and demotes only the shared line. ``name`` is handled
# separately (only when it is NOT a person-name column -- an employer / org /
# department name), so a real first/last-name field is never eligible.
_WORKPLACE_ATTRIBUTE_TYPES = frozenset({"address", "phone"})


def attribute_demotion_enabled() -> bool:
    """Whether to demote a WEIGHTED FUZZY attribute field (clinic address, org
    name) whose shared values do not co-agree on person identity. Default OFF --
    a behavior change pending the DQbench/Febrl/NCVR accuracy sweep; byte-
    identical when off. Enable: GOLDENMATCH_ATTRIBUTE_DEMOTION=1 (or
    true/yes/on/enabled, case-insensitive)."""
    return os.environ.get("GOLDENMATCH_ATTRIBUTE_DEMOTION", "0").strip().lower() in {
        "1", "true", "yes", "on", "enabled",
    }


def should_demote_attribute_field(
    df: pl.DataFrame | None,
    candidate_col: str,
    col_type: str | None,
    name_basket: list[tuple[str, bool]],
    *,
    is_person_name: bool,
    min_shared_pairs: int = _MIN_SHARED_PAIRS,
    max_pairs: int = _MAX_PAIRS,
) -> bool:
    """True => demote a matchkey use of candidate_col to blocking-only (applies
    to an exact OR a weighted-fuzzy use).

    A workplace / locality attribute (a clinic ``address``, a shared clinic
    ``phone`` line, or an employer / org ``company`` name) shared by colleagues
    is NOT person-identity evidence: records sharing its value do not co-agree on
    the PERSON NAME. Reuses the
    #1351 co-agreement machinery, but with two deliberate narrowings vs
    :func:`should_veto_exact`:

    * **Scope** -- only ATTRIBUTE-typed fields are eligible (``address``, or a
      ``name``-typed field that is NOT a person name, i.e. an org/employer/dept
      name). A real first/last-name or structured-identity field can never be
      demoted.
    * **Basket** -- co-agreement is measured against PERSON-NAME columns only
      (``name_basket``), NOT the broad identity basket. The broad basket is wrong
      here: colleagues at one clinic trivially agree on constant dataset metadata
      mis-typed as ``identifier`` (a publication/franchise code) and on a shared
      workplace ``phone``, which masks the fact that they are different people.
      The person name is the clean anchor: do records sharing this attribute
      value share a NAME? If not, the attribute is workplace/locality, not
      identity.

    Data-measured (no name allowlist), so it is a no-op where the attribute is
    genuinely identity-correlated (shared-value records DO co-agree on name ->
    power >= tau -> kept). Fail-safe = keep (False) on: flag off, df is None, a
    non-attribute field, an empty name basket (no person-name anchor), or
    insufficient shared-value support.
    """
    if not attribute_demotion_enabled() or df is None:
        return False
    is_attribute = col_type in _WORKPLACE_ATTRIBUTE_TYPES or (
        col_type == "name" and not is_person_name
    )
    if not is_attribute:
        return False
    basket = [(c, f) for (c, f) in name_basket if c != candidate_col and c in df.columns]
    if not basket:
        return False
    power, support = discriminative_power(df, candidate_col, basket, max_pairs=max_pairs)
    if support < min_shared_pairs:
        return False
    return power < tau()
