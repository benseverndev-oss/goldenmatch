"""Lightweight domain-pack auto-detection from column names."""
from __future__ import annotations

import re

from goldencheck_types import list_domains, load_domain

DEFAULT_MIN_SCORE = 0.3

# Split column / hint identifiers on the usual word separators. Keeping the
# split conservative — `_-.` plus whitespace — matches how SQL columns,
# CSV headers, and Python identifiers are conventionally tokenized.
_TOKEN_SPLIT = re.compile(r"[_\-.\s]+")


def _tokens(s: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(s.lower()) if t]


def _hint_matches(hint: str, col: str) -> bool:
    """True iff ``hint``'s tokens appear as a contiguous run in ``col``'s tokens.

    Replaces the prior symmetric substring check ``h in c or c in h``, which
    fired on partial overlaps — a 2-char hint like ``"id"`` matched
    ``account_id``, ``paid``, ``void_id``, ``id_card`` indiscriminately and
    the reverse direction let any short column match every long hint.
    Token-boundary matching keeps ``"npi"`` matching ``provider_npi`` (token
    membership) while rejecting ``"npi"`` against ``"npiece"`` (no boundary).
    """
    h_tokens = _tokens(hint)
    c_tokens = _tokens(col)
    if not h_tokens or not c_tokens:
        return False
    n = len(h_tokens)
    return any(c_tokens[i : i + n] == h_tokens for i in range(len(c_tokens) - n + 1))


def detect_domain(
    df,
    candidates: list[str] | None = None,
    min_score: float = DEFAULT_MIN_SCORE,
) -> str | None:
    """Pick the domain pack whose name_hints best match this df's columns.

    Returns ``None`` if no candidate scores at or above ``min_score``, or if
    multiple candidates tie for top score (refuses to silently pick one).
    The ``generic`` pack is always excluded from auto-detection.
    """
    columns = [str(c) for c in df.columns]
    domains = candidates or [d for d in list_domains() if d != "generic"]

    scored: list[tuple[str, float]] = []
    for d in domains:
        pack = load_domain(d)
        all_hints = {
            h
            for spec in pack.types.values()
            for h in spec.name_hints
        }
        if not all_hints:
            continue

        hits = sum(
            1 for c in columns if any(_hint_matches(h, c) for h in all_hints)
        )
        score = hits / max(len(columns), 1)
        scored.append((d, score))

    if not scored:
        return None

    best_score = max(s for _, s in scored)
    if best_score < min_score:
        return None
    top = [d for d, s in scored if s == best_score]
    # Tie-break: refuse to pick. Caller can fall through to "generic" with
    # explicit knowledge that detection was ambiguous, vs us silently
    # picking whichever sorted first.
    return top[0] if len(top) == 1 else None
