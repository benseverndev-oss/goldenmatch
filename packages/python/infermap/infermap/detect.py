"""Lightweight domain-pack auto-detection from column names."""
from __future__ import annotations

from goldencheck_types import list_domains, load_domain

DEFAULT_MIN_SCORE = 0.3


def detect_domain(
    df,
    candidates: list[str] | None = None,
    min_score: float = DEFAULT_MIN_SCORE,
) -> str | None:
    """Pick the domain pack whose name_hints best match this df's columns.

    Returns ``None`` if no candidate scores at or above ``min_score``.
    The ``generic`` pack is always excluded from auto-detection.
    """
    cols_lc = [str(c).lower() for c in df.columns]
    domains = candidates or [d for d in list_domains() if d != "generic"]

    best: str | None = None
    best_score = 0.0

    for d in domains:
        pack = load_domain(d)
        all_hints = {
            h.lower()
            for spec in pack.types.values()
            for h in spec.name_hints
        }
        if not all_hints:
            continue

        hits = sum(
            1 for c in cols_lc if any(h in c or c in h for h in all_hints)
        )
        score = hits / max(len(cols_lc), 1)
        if score > best_score:
            best, best_score = d, score

    return best if best_score >= min_score else None
