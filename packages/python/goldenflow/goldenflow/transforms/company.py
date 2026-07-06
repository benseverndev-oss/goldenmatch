"""Company/organization dedup-normalization transforms.

Native-first over ``goldenflow-core::company``; the ``_*_py`` functions are the
byte-exact pure-Python references (byte-parity harness,
``tests/parity/identifiers_corpus.jsonl``).
"""
from __future__ import annotations

import polars as pl

from goldenflow.transforms import register_transform
from goldenflow.transforms._native import (
    company_extract_legal_native,
    company_normalize_native,
    company_strip_legal_native,
)

# Legal-form suffix tokens (lowercase, punctuation-free -- compared against a
# token's alnum-only key). Keep byte-for-byte in lockstep with goldenflow-core's
# LEGAL_TOKENS (company.rs) and the TS fallback (company.ts).
_LEGAL_TOKENS = frozenset(
    {
        "inc", "incorporated", "llc", "llp", "lp", "ltd", "limited",
        "corp", "corporation", "co", "company", "companies", "gmbh", "ag",
        "sa", "ab", "plc", "pc", "pllc", "nv", "bv", "oy", "oyj", "asa",
        "kg", "kgaa", "srl", "spa", "pty", "sarl", "aps", "kk", "sas", "sl",
        "sro", "doo", "pvt", "bhd", "sdn", "ulc",
    }
)

_TRAILING_TRIM = " \t\r\n.,"


def _legal_key(tok: str) -> str:
    """Alnum-only, lowercased comparison key for a token (``L.L.C.`` -> ``llc``)."""
    return "".join(c.lower() for c in tok if c.isascii() and c.isalnum())


def _is_legal(tok: str) -> bool:
    key = _legal_key(tok)
    return bool(key) and key in _LEGAL_TOKENS


def _company_normalize_py(val: str | None) -> str | None:
    if val is None:
        return None
    trimmed = val.strip()
    if not trimmed:
        return None
    lower = trimmed.lower()
    # Keep ASCII alnum + '&'; DROP '.' (acronym-preserving); else word break.
    cleaned_chars: list[str] = []
    for c in lower:
        if (c.isascii() and c.isalnum()) or c == "&":
            cleaned_chars.append(c)
        elif c != ".":
            cleaned_chars.append(" ")
    tokens = "".join(cleaned_chars).split()
    if tokens and tokens[0] == "the":
        tokens.pop(0)
    while tokens and (tokens[-1] == "&" or _is_legal(tokens[-1])):
        tokens.pop()
    return " ".join(tokens)


def _company_strip_legal_py(val: str | None) -> str | None:
    if val is None:
        return None
    trimmed = val.strip()
    if not trimmed:
        return None
    t = trimmed
    while True:
        core = t.rstrip(_TRAILING_TRIM)
        idx = _last_ws(core)
        if idx == -1:
            head, candidate = "", core
        else:
            head, candidate = core[:idx], core[idx + 1 :]
        if _is_legal(candidate):
            t = head
        else:
            t = core
            break
    return t.strip()


def _company_extract_legal_py(val: str | None) -> str | None:
    if val is None:
        return None
    core = val.strip().rstrip(_TRAILING_TRIM)
    if not core:
        return None
    idx = _last_ws(core)
    last = core if idx == -1 else core[idx + 1 :]
    key = _legal_key(last)
    return key if key in _LEGAL_TOKENS else None


def _last_ws(s: str) -> int:
    """Index of the last ASCII/Unicode-whitespace char, or -1 (mirrors Rust
    ``rfind(char::is_whitespace)``)."""
    for i in range(len(s) - 1, -1, -1):
        if s[i].isspace():
            return i
    return -1


@register_transform(
    name="company_normalize",
    input_types=["company", "organization", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def company_normalize(series: pl.Series) -> pl.Series:
    """Composite company dedup key: lowercase, drop leading 'the', strip legal
    suffixes. Native-first over goldenflow-core."""
    native = company_normalize_native()
    if native is not None:
        return native(series)
    return series.map_elements(_company_normalize_py, return_dtype=pl.Utf8)


@register_transform(
    name="company_strip_legal",
    input_types=["company", "organization", "string"],
    auto_apply=False,
    priority=45,
    mode="series",
)
def company_strip_legal(series: pl.Series) -> pl.Series:
    """Strip trailing legal-form suffixes, preserving the core name's case.
    Native-first over goldenflow-core."""
    native = company_strip_legal_native()
    if native is not None:
        return native(series)
    return series.map_elements(_company_strip_legal_py, return_dtype=pl.Utf8)


@register_transform(
    name="company_extract_legal",
    input_types=["company", "organization", "string"],
    auto_apply=False,
    priority=40,
    mode="series",
)
def company_extract_legal(series: pl.Series) -> pl.Series:
    """Extract the canonical legal-form token (``inc``/``llc``/...) or null.
    Native-first over goldenflow-core."""
    native = company_extract_legal_native()
    if native is not None:
        return native(series)
    return series.map_elements(_company_extract_legal_py, return_dtype=pl.Utf8)
