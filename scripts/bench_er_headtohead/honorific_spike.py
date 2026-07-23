"""Honorific-normalization spike — strip title/honorific tokens from name fields.

THROWAWAY spike (not shipped). The FS over-merge diagnostic on ``historical_50k``
(Wikidata historical people) showed the false merges concentrate on honorific
*tokens* that leaked into the name fields, not real names:

    first_name  98.9% of FP pairs agree · top values: sir(144259), 1st(2269), ...
    surname     96.8% of FP pairs agree · top values: baronet(144727), bt.(1642), ...

Term-frequency down-weighting came back byte-neutral there because the name
coltype already routes through ``name_freq_weighted_jw`` (TF self-neutralizes).
The residual lever this spike tests: remove the honorific tokens *before* FS sees
the field, so an agreement on ``sir``/``baronet`` stops carrying match weight.

Gated by ``GM_SPIKE_STRIP_HONORIFICS`` in ``run_panel.py`` (default OFF ==
byte-identical baseline). The A/B verdict on real data is the only valid
instrument — no local historical_50k, and stripping cuts agreement from *true*
historical-knight merges too, so the net F1 effect must be measured, not argued.
"""
from __future__ import annotations

import re

import polars as pl

# Honorific / title / rank / suffix tokens that leak into name fields on
# historical (Wikidata) data. Matched case-insensitively, token-wise, with
# trailing punctuation and ordinal suffixes stripped before comparison.
_HONORIFIC_TOKENS: frozenset[str] = frozenset(
    {
        # courtesy titles
        "mr", "mrs", "ms", "miss", "mstr", "master",
        # academic / professional
        "dr", "prof", "professor",
        # religious
        "rev", "revd", "reverend", "fr", "father", "st", "saint",
        "pope", "cardinal", "bishop", "archbishop", "deacon",
        # honorifics / knighthoods (the historical_50k drivers)
        "sir", "dame", "hon", "honourable", "honorable",
        "knight", "kt", "bt", "baronet",
        # peerage / nobility ranks
        "lord", "lady", "baron", "baroness", "earl", "count", "countess",
        "duke", "duchess", "viscount", "viscountess",
        "marquess", "marquis", "marchioness",
        # royalty
        "king", "queen", "prince", "princess", "emperor", "empress",
        "tsar", "czar", "kaiser", "sultan", "shah", "emir", "sheikh",
        # military rank
        "gen", "general", "col", "colonel", "maj", "major",
        "capt", "captain", "lt", "lieutenant", "sgt", "sergeant",
        "adm", "admiral", "cmdr", "commander", "brig", "brigadier",
        "marshal", "fieldmarshal",
        # generational / post-nominal suffixes
        "jr", "sr", "esq", "esquire",
        "phd", "md", "dds", "dvm", "do",
        # roman-numeral regnal ordinals (Henry VIII etc.)
        "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
        "xi", "xii", "xiii", "xiv", "xv", "xvi", "xvii", "xviii",
    }
)

# Ordinal words / numerals that appear as standalone name tokens ("1st", "2nd").
_ORDINAL_RE = re.compile(r"^\d+(st|nd|rd|th)?$", re.IGNORECASE)
# Strip leading/trailing punctuation from a token before the token test.
_PUNCT_STRIP_RE = re.compile(r"^[^\w]+|[^\w]+$")


def _is_honorific(token: str) -> bool:
    bare = _PUNCT_STRIP_RE.sub("", token).lower()
    if not bare:
        return True  # pure punctuation → drop
    if bare in _HONORIFIC_TOKENS:
        return True
    if _ORDINAL_RE.match(bare):
        return True
    return False


def strip_honorifics(value: str | None) -> str | None:
    """Drop honorific/title/rank tokens from a name string.

    Token-wise, case-insensitive, punctuation-tolerant. Returns ``None`` when
    nothing survives (a name field that was *only* an honorific, e.g. ``"Sir"``
    or ``"Baronet"``) so the FS scorer treats it as a missing value rather than
    a spurious empty-string agreement.
    """
    if value is None:
        return None
    tokens = value.split()
    if not tokens:
        return None
    kept = [t for t in tokens if not _is_honorific(t)]
    residual = " ".join(kept).strip()
    return residual or None


# Name columns to normalize on the bench datasets (historical_50k uses
# first_name/surname; the others are covered defensively if present).
_NAME_COLUMNS: tuple[str, ...] = (
    "first_name", "surname", "last_name", "given_name", "family_name", "name",
)


def strip_honorifics_frame(df: pl.DataFrame) -> pl.DataFrame:
    """Apply :func:`strip_honorifics` to every present name column, in place of
    the original values, preserving row order and every other column."""
    present = [c for c in _NAME_COLUMNS if c in df.columns]
    if not present:
        return df
    return df.with_columns(
        pl.col(c)
        .map_elements(strip_honorifics, return_dtype=pl.String)
        .alias(c)
        for c in present
    )
