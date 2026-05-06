"""Lightweight domain-pack auto-detection from column names."""
from __future__ import annotations

import re

from goldencheck_types import DetectionResult, list_domains, load_domain

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
    multiple candidates tie for top score. Use ``detect_domain_detailed``
    when you want to distinguish those two cases or see the runner-up.
    The ``generic`` pack is always excluded from auto-detection.
    """
    return detect_domain_detailed(df, candidates, min_score).domain


def detect_domain_detailed(
    df,
    candidates: list[str] | None = None,
    min_score: float = DEFAULT_MIN_SCORE,
) -> DetectionResult:
    """Auto-detect with full diagnostic info.

    Returns a :class:`DetectionResult` carrying:
      - ``domain`` — the picked name, or None when no decision was made
      - ``score`` — top score (regardless of whether it was picked)
      - ``runner_up`` / ``runner_up_score`` — second-best candidate
      - ``reason`` — one of "confident", "tie", "below_min_score", "no_data"

    Callers like ``goldenpipe.stages.infer_schema`` use this to surface
    "auto-detected with confidence" vs "auto-fallback because tied" in
    the InferredSchema's confidence field and evidence map.
    """
    columns = [str(c) for c in df.columns]
    if not columns:
        return DetectionResult(
            domain=None, score=0.0, runner_up=None, runner_up_score=0.0,
            reason="no_data",
        )

    domains = candidates or [d for d in list_domains() if d != "generic"]
    scored: list[tuple[str, float]] = []
    for d in domains:
        pack = load_domain(d)
        all_hints = {h for spec in pack.types.values() for h in spec.name_hints}
        if not all_hints:
            continue
        hits = sum(
            1 for c in columns if any(_hint_matches(h, c) for h in all_hints)
        )
        scored.append((d, hits / max(len(columns), 1)))

    if not scored:
        return DetectionResult(
            domain=None, score=0.0, runner_up=None, runner_up_score=0.0,
            reason="no_data",
        )

    scored.sort(key=lambda x: x[1], reverse=True)
    best_name, best_score = scored[0]
    runner_name, runner_score = (scored[1] if len(scored) > 1 else (None, 0.0))

    if best_score < min_score:
        return DetectionResult(
            domain=None, score=best_score,
            runner_up=runner_name, runner_up_score=runner_score,
            reason="below_min_score",
        )

    top_count = sum(1 for _, s in scored if s == best_score)
    if top_count > 1:
        return DetectionResult(
            domain=None, score=best_score,
            runner_up=runner_name, runner_up_score=runner_score,
            reason="tie",
        )

    return DetectionResult(
        domain=best_name, score=best_score,
        runner_up=runner_name, runner_up_score=runner_score,
        reason="confident",
    )
