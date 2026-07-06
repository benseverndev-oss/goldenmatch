"""Lightweight domain-pack auto-detection from column names."""
from __future__ import annotations

import re

from goldencheck_types import DetectionResult, list_domains, load_domain

from infermap._native_loader import native_enabled, native_module

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
    # Load packs (host / "smart pipe"): (name, deduped name_hints) IN iteration order.
    # The scoring + decision ("dumb kernel") is single-sourced via `_detect_core`.
    domain_names = candidates or [d for d in list_domains() if d != "generic"]
    domains: list[tuple[str, list[str]]] = []
    for d in domain_names:
        pack = load_domain(d)
        hints = list({h for spec in pack.types.values() for h in spec.name_hints})
        domains.append((d, hints))

    domain, score, runner_up, runner_up_score, reason = _detect_core(columns, domains, min_score)
    return DetectionResult(
        domain=domain, score=score,
        runner_up=runner_up, runner_up_score=runner_up_score,
        reason=reason,
    )


def _detect_core(
    columns: list[str],
    domains: list[tuple[str, list[str]]],
    min_score: float,
) -> tuple[str | None, float, str | None, float, str]:
    """Dispatch the detect scoring+decision to the native kernel when gated, else pure."""
    if native_enabled("detect_domain"):
        return tuple(native_module().detect_domain(columns, domains, min_score))
    return _detect_core_pure(columns, domains, min_score)


def _detect_core_pure(
    columns: list[str],
    domains: list[tuple[str, list[str]]],
    min_score: float,
) -> tuple[str | None, float, str | None, float, str]:
    """Byte-identical reference for ``infermap-core::detect_domain``.

    Returns ``(domain, score, runner_up, runner_up_score, reason)``. Empty columns or
    no scorable domain (all hint-less) => ``no_data``. Direction-aware sort is stable
    (Python ``sort(reverse=True)`` keeps ties in host order).
    """
    if not columns:
        return (None, 0.0, None, 0.0, "no_data")
    scored: list[tuple[str, float]] = []
    for name, hints in domains:
        if not hints:
            continue
        hits = sum(1 for c in columns if any(_hint_matches(h, c) for h in hints))
        scored.append((name, hits / len(columns)))
    if not scored:
        return (None, 0.0, None, 0.0, "no_data")
    scored.sort(key=lambda x: x[1], reverse=True)
    best_name, best_score = scored[0]
    runner_name, runner_score = (scored[1] if len(scored) > 1 else (None, 0.0))
    if best_score < min_score:
        return (None, best_score, runner_name, runner_score, "below_min_score")
    if sum(1 for _, s in scored if s == best_score) > 1:
        return (None, best_score, runner_name, runner_score, "tie")
    return (best_name, best_score, runner_name, runner_score, "confident")
