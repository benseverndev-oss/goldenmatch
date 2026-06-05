"""LLM-assisted golden-strategy picks for ambiguous fields (closes #430).

When the heuristic refiner (`_pick_strategy_for_field`) returns None
for a field AND the user opted in via
`GoldenRulesConfig.use_llm_for_ambiguous=True` AND budget allows,
dispatch ONE LLM call per ambiguous field to pick a strategy.

Spec: docs/superpowers/specs/2026-05-22-golden-rules-intelligence-layer-2-design.md  # noqa: E501

Design notes
------------
- ONE call per (dataset, field) -- cached so re-runs don't re-call.
- BudgetTracker integration: skips when budget would be exceeded;
  soft-fails (returns None) on any error.
- Strategy whitelist: the LLM must return one of VALID_STRATEGIES;
  unknown responses are rejected, not propagated.
- LLM caller pluggable: tests pass a stub via `llm_caller=`; default
  auto-detects an OpenAI or Anthropic provider via existing helpers in
  ``llm_scorer``. No network call when no key is set (returns None).
"""
from __future__ import annotations

import logging
import random
from collections.abc import Callable

from goldenmatch.config.schemas import VALID_STRATEGIES

logger = logging.getLogger(__name__)


# Strategies the LLM is allowed to choose from. Mirrors the prompt
# template enumeration in issue #430.
_LLM_OFFER_STRATEGIES: tuple[str, ...] = (
    "most_complete",
    "majority_vote",
    "first_non_null",
    "most_recent",
    "source_priority",
    "longest_value",
    "unanimous_or_null",
    "confidence_majority",
)

# Per-call token budget. Field-strategy picks are tiny -- the prompt
# is < 500 tokens, the response is one strategy name + one-line
# rationale (< 50 tokens). 600 is comfortable; reduces risk of
# exhausting the suite-level budget.
_TOKEN_ESTIMATE_PER_CALL = 600

# Sample shape: how many clusters to sample, how many values per cluster.
_SAMPLE_CLUSTERS = 5
_SAMPLE_VALUES_PER_CLUSTER = 3


__all__ = [
    "pick_strategy_via_llm",
    "format_prompt",
    "parse_llm_response",
]


def format_prompt(
    field: str,
    col_type: str,
    samples: list[tuple[int, list[str]]],
) -> str:
    """Render the LLM prompt from #430's template.

    Args:
        field: column name.
        col_type: semantic column type from the profiler
            (``"string"``, ``"date"``, ``"identifier"``, etc.). Use
            ``"unknown"`` when no classification exists.
        samples: list of ``(cluster_id, [value, value, ...])`` tuples.
            Values are the raw within-cluster strings -- never
            preprocessed.

    Returns:
        Prompt string ready for `llm_caller(prompt)`.
    """
    strategies_list = ", ".join(_LLM_OFFER_STRATEGIES)
    sample_lines = []
    for cluster_id, values in samples:
        joined = ", ".join(repr(v) for v in values)
        sample_lines.append(f"- Cluster {cluster_id}: {joined}")
    samples_block = "\n".join(sample_lines) if sample_lines else "(no samples)"

    return (
        "You are picking a golden-record consolidation strategy for a "
        "database field.\n"
        "Given the column name, a sample of values, and the available "
        "strategies, choose the best fit.\n\n"
        f"Field: {field}\n"
        f"Column type: {col_type}\n"
        f"Sample values (up to {_SAMPLE_CLUSTERS} from random clusters):\n"
        f"{samples_block}\n\n"
        f"Available strategies: {strategies_list}.\n\n"
        "Respond with ONE strategy name (lowercase, snake_case) "
        "followed by a one-line rationale, separated by a colon. "
        "Example: `most_recent: Address-like field with frequent "
        "updates; latest value most likely current.`"
    )


def parse_llm_response(text: str) -> str | None:
    """Extract a valid strategy name from the LLM response.

    Accepts the documented `strategy: rationale` shape; falls back to
    looking for any valid strategy token in the response. Returns None
    when no valid strategy can be identified.
    """
    if not text:
        return None
    head = text.strip().split(":", 1)[0].strip().lower()
    if head in VALID_STRATEGIES:
        return head
    # Fallback: scan the whole response for any valid strategy token.
    lowered = text.lower()
    for s in _LLM_OFFER_STRATEGIES:
        if s in lowered:
            return s
    return None


def _build_samples(
    field: str,
    clusters_by_id: dict[int, list[str]],
    *,
    seed: int = 42,
) -> list[tuple[int, list[str]]]:
    """Pick up to `_SAMPLE_CLUSTERS` clusters; emit up to
    `_SAMPLE_VALUES_PER_CLUSTER` non-null distinct values per cluster.

    Cluster sampling is deterministic via `random.Random(seed)` to keep
    LLM cache keys stable across runs with the same dataset shape.
    """
    if not clusters_by_id:
        return []
    rng = random.Random(seed)
    cluster_ids = list(clusters_by_id.keys())
    rng.shuffle(cluster_ids)
    picked = cluster_ids[:_SAMPLE_CLUSTERS]
    out: list[tuple[int, list[str]]] = []
    for cid in picked:
        values = clusters_by_id[cid]
        # Deduplicate + keep order; drop empties.
        seen: set[str] = set()
        distinct: list[str] = []
        for v in values:
            if v is None:
                continue
            s = str(v).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            distinct.append(s)
            if len(distinct) >= _SAMPLE_VALUES_PER_CLUSTER:
                break
        if distinct:
            out.append((cid, distinct))
    return out


def _default_llm_caller(prompt: str) -> str | None:
    """Auto-detect provider via llm_scorer helpers and dispatch.

    Returns the LLM response text, or None when no provider is
    configured. Soft-fails (returns None) on any error.
    """
    try:
        from goldenmatch.core.llm_scorer import (
            _call_anthropic,
            _call_openai,
            _detect_provider,
        )
    except Exception as exc:  # pragma: no cover -- defensive
        logger.debug("llm_scorer not importable: %s", exc)
        return None

    provider, api_key = _detect_provider()
    if not provider or not api_key:
        logger.debug("no LLM provider configured; skipping LLM strategy pick")
        return None

    try:
        if provider == "openai":
            text, _in_tokens, _out_tokens = _call_openai(
                prompt=prompt,
                api_key=api_key,
                model="gpt-4o-mini",
            )
        else:
            text, _in_tokens, _out_tokens = _call_anthropic(
                prompt=prompt,
                api_key=api_key,
                model="claude-haiku-4-5-20251001",
            )
        return text
    except Exception:
        # Log a constant only. Both `exc` (provider clients echo the api key
        # into error text) and `provider` (element 0 of `_detect_provider`'s
        # (label, key) tuple, which CodeQL taints from the OPENAI_API_KEY
        # source) carry credential taint to this sink (CodeQL #302).
        logger.warning("LLM strategy pick failed")
        return None


def pick_strategy_via_llm(
    field: str,
    col_type: str,
    clusters_by_id: dict[int, list[str]],
    *,
    dataset: str = "default",
    budget: object | None = None,
    cache: dict[tuple[str, str], str | None] | None = None,
    llm_caller: Callable[[str], str | None] | None = None,
    seed: int = 42,
) -> str | None:
    """Pick a golden-strategy for `field` via one LLM call.

    Args:
        field: column name.
        col_type: semantic type from profilers (``"string"``,
            ``"date"``, ``"identifier"``, ``"unknown"`` etc.).
        clusters_by_id: ``{cluster_id: [value, value, ...]}`` -- raw
            within-cluster string values. A sample (deterministic) is
            shown to the LLM.
        dataset: dataset key for cache scoping. Default ``"default"``.
        budget: optional BudgetTracker instance. When provided,
            ``budget.can_afford(_TOKEN_ESTIMATE_PER_CALL)`` is checked
            before dispatching the call. Soft-fails to None when
            budget is exhausted.
        cache: optional ``{(dataset, field): strategy_or_None}`` dict.
            When provided + the (dataset, field) key is present, the
            cached value is returned with NO LLM call. When absent,
            the dispatched call's result is written back to the cache.
        llm_caller: pluggable LLM callable. Default auto-detects an
            OpenAI / Anthropic provider via existing helpers. Tests
            should pass a stub.
        seed: RNG seed for cluster sampling. Default 42.

    Returns:
        A strategy name from ``VALID_STRATEGIES`` on success, or
        ``None`` when no LLM provider, budget exhausted, or invalid
        response.
    """
    cache_key = (dataset, field)
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    # Budget gate.
    if budget is not None:
        try:
            can_afford = budget.can_afford(_TOKEN_ESTIMATE_PER_CALL)
        except Exception as exc:  # pragma: no cover -- defensive
            logger.warning("budget.can_afford failed: %s", exc)
            can_afford = True
        if not can_afford:
            logger.info(
                "LLM strategy pick skipped for field=%r: budget exhausted",
                field,
            )
            if cache is not None:
                cache[cache_key] = None
            return None

    samples = _build_samples(field, clusters_by_id, seed=seed)
    if not samples:
        logger.debug(
            "no samples for field=%r in cluster dict; skipping LLM",
            field,
        )
        if cache is not None:
            cache[cache_key] = None
        return None

    prompt = format_prompt(field, col_type, samples)
    caller = llm_caller or _default_llm_caller

    try:
        response = caller(prompt)
    except Exception as exc:
        logger.warning("LLM call raised for field=%r: %s", field, exc)
        if cache is not None:
            cache[cache_key] = None
        return None

    if not response:
        if cache is not None:
            cache[cache_key] = None
        return None

    strategy = parse_llm_response(response)
    if strategy:
        logger.info(
            "LLM picked strategy=%s for field=%r (dataset=%s)",
            strategy, field, dataset,
        )
    else:
        logger.warning(
            "LLM response for field=%r did not match any valid strategy: %r",
            field, response[:200],
        )

    # Charge the budget AFTER a successful response (matches the
    # existing pattern in llm_scorer where charge happens post-call).
    if budget is not None and strategy is not None:
        try:
            budget.charge(_TOKEN_ESTIMATE_PER_CALL, model="llm-strategy")
        except Exception as exc:  # pragma: no cover -- defensive
            logger.debug("budget.charge failed: %s", exc)

    if cache is not None:
        cache[cache_key] = strategy
    return strategy
