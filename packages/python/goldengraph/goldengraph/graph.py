"""Slice 4c GoldenGraph facade + the ER ExecutionPlan surface.

`plan_er_execution` surfaces the goldenmatch controller-v3 ExecutionPlan the ER controller WOULD pick
for the aggregate corpus ER workload at this scale. ADVISORY: the per-doc build resolver still runs
small per document; this plan is the scale SIGNAL (flip to a batched/distributed resolution path at
scale), not yet consumed by the build. goldenmatch is imported lazily so the package imports without it.
"""
from __future__ import annotations

_AVG_ENTITIES_PER_DOC = 8  # rough heuristic when the caller gives no corpus_records


def _estimate_records(docs) -> int:
    return _AVG_ENTITIES_PER_DOC * sum(1 for _ in docs)


def _representative_complexity(n_rows_full: int):
    # The DEFAULT_RULES scale predicates key off n_rows_full + runtime.available_ram_gb (verified in
    # autoconfig_planner_rules: simple/fast_box/bucket/chunked thresholds), NOT the data-shape sub-
    # profiles -- so a default ComplexityProfile is sufficient and n_rows_full drives the separation.
    from goldenmatch.core.complexity_profile import ComplexityProfile

    return ComplexityProfile()


def plan_er_execution(docs, *, corpus_records: int | None = None):
    """Return the ER controller's scale ExecutionPlan for this corpus (ADVISORY -- the build does not
    consume it; it is the signal to flip to a batched/distributed resolution path at scale)."""
    n_rows_full = corpus_records if corpus_records is not None else _estimate_records(docs)
    from goldenmatch.core.autoconfig_planner import apply_planner_rules

    # Import DEFAULT_RULES directly (not the in-module _default_rules() wrapper, which exists only to
    # break goldenmatch's OWN import cycle and is absent on older goldenmatch builds). From outside
    # goldenmatch there is no cycle, and this resolves on both current and older installs.
    from goldenmatch.core.autoconfig_planner_rules import DEFAULT_RULES
    from goldenmatch.core.runtime_profile import capture_runtime_profile

    runtime = capture_runtime_profile()
    profile = _representative_complexity(n_rows_full)
    return apply_planner_rules(profile, runtime, n_rows_full, DEFAULT_RULES)
