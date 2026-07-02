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
from typing import Any

import polars as pl

_IDENTITY_BASKET_TYPES = frozenset({"name", "multi_name", "email", "phone", "identifier"})

_TAU_DEFAULT = 0.5
_MIN_SHARED_PAIRS = 20
_MAX_PAIRS = 200


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


def identity_basket(candidate_col: str, profiles: list[Any]) -> list[str]:
    """Other columns whose col_type is an identity signal (excludes candidate)."""
    return [
        p.name
        for p in profiles
        if p.name != candidate_col and getattr(p, "col_type", None) in _IDENTITY_BASKET_TYPES
    ]


def _norm(v: Any) -> str | None:
    """Normalize a cell for equality: str -> stripped lower; blank/None -> None."""
    if v is None:
        return None
    s = str(v).strip().lower()
    return s or None


def discriminative_power(
    df: pl.DataFrame,
    candidate_col: str,
    basket: list[str],
    *,
    max_pairs: int = _MAX_PAIRS,
) -> tuple[float, int]:
    """Mean co-agreement over shared-value pairs, and support (n pairs measured).

    Groups df by candidate_col; for value-groups with >=2 rows, forms up to
    max_pairs record-pairs deterministically (row 0 paired with rows 1..k-1
    within each group, groups visited in sorted-value order). Per pair,
    agreement = (# basket fields where BOTH cells are non-null and
    normalized-equal) / (# basket fields where BOTH are non-null); pairs with no
    jointly-populated basket field are skipped. Returns (mean over measured
    pairs, count of measured pairs); (0.0, 0) when basket empty, candidate
    absent, or no measurable shared-value pair exists.
    """
    if not basket or candidate_col not in df.columns:
        return 0.0, 0
    keep = [candidate_col, *[c for c in basket if c in df.columns]]
    if len(keep) < 2:
        return 0.0, 0
    sub = df.select(keep)
    basket_cols = keep[1:]

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
            for c in basket_cols:
                a, b = _norm(anchor[c]), _norm(other[c])
                if a is None or b is None:
                    continue
                comparable += 1
                if a == b:
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
