"""Concrete planner rules for controller v3.

Spec §Decision rules; one section per rule:
docs/superpowers/specs/2026-05-15-controller-v3-planner-design.md.

This module owns the **planner's** rule table (deciding the execution
plan -- backend, chunk_size, max_workers, etc.). It is distinct from
``autoconfig_rules`` which owns the v1.10/v1.11 HeuristicRefitPolicy
rules that mutate the GoldenMatchConfig during controller iteration.
"""
from __future__ import annotations

from goldenmatch.core.autoconfig_planner import PlannerRule
from goldenmatch.core.complexity_profile import ComplexityProfile
from goldenmatch.core.execution_plan import ExecutionPlan
from goldenmatch.core.runtime_profile import RuntimeProfile

# ── Rule 1: pathological inputs (spec §Rule 1) ──────────────────────────────


def _is_pathological(
    profile: ComplexityProfile,
    runtime: RuntimeProfile,
    n_rows_full: int,
) -> bool:
    """Spec §Rule 1: n_rows <= 1. The controller's earlier paths raise
    ConfigValidationError for n_rows == 0 / single column / all-null;
    this rule catches the trivial n_rows == 1 case so the planner's
    decision table reads completely (defensive)."""
    return n_rows_full <= 1


def _pathological_plan(
    profile: ComplexityProfile,
    runtime: RuntimeProfile,
    n_rows_full: int,
) -> ExecutionPlan:
    return ExecutionPlan(
        backend="polars-direct",
        max_workers=1,
        rule_name="plan_pathological",
    )


rule_pathological = PlannerRule(
    name="plan_pathological",
    predicate=_is_pathological,
    action=_pathological_plan,
)


# ── Rule 2: simple plan (spec §Rule 2) ──────────────────────────────────────

# Spec thresholds pinned as named constants so calibration is one-liner.
SIMPLE_PLAN_MAX_ROWS = 100_000
SIMPLE_PLAN_MAX_PAIRS = 50_000_000


def _is_simple_eligible(
    profile: ComplexityProfile,
    runtime: RuntimeProfile,
    n_rows_full: int,
) -> bool:
    """Spec §Rule 2: ``n_rows < 100_000 AND estimated_pair_count < 50_000_000``."""
    return (
        n_rows_full < SIMPLE_PLAN_MAX_ROWS
        and profile.blocking.estimated_pair_count < SIMPLE_PLAN_MAX_PAIRS
    )


def _simple_plan(
    profile: ComplexityProfile,
    runtime: RuntimeProfile,
    n_rows_full: int,
) -> ExecutionPlan:
    return ExecutionPlan(
        backend="polars-direct",
        chunk_size=None,
        max_workers=min(4, runtime.cpu_count),
        pair_spill_threshold=None,
        clustering_strategy="in_memory",
        rule_name="plan_selected_simple",
    )


rule_simple_plan = PlannerRule(
    name="plan_selected_simple",
    predicate=_is_simple_eligible,
    action=_simple_plan,
)


# ── Rule 3: fast-box plan (spec §Rule 3) ────────────────────────────────────

FAST_BOX_MIN_RAM_GB = 32.0


def _is_fast_box_eligible(
    profile: ComplexityProfile,
    runtime: RuntimeProfile,
    n_rows_full: int,
) -> bool:
    """Spec §Rule 3: large row count but sparse pair count on a fat machine.

    Fires when the simple plan's row ceiling is exceeded but pair count
    still fits in RAM and the machine has >= 32 GB available.
    """
    return (
        n_rows_full >= SIMPLE_PLAN_MAX_ROWS
        and profile.blocking.estimated_pair_count < SIMPLE_PLAN_MAX_PAIRS
        and runtime.available_ram_gb >= FAST_BOX_MIN_RAM_GB
    )


def _fast_box_plan(
    profile: ComplexityProfile,
    runtime: RuntimeProfile,
    n_rows_full: int,
) -> ExecutionPlan:
    return ExecutionPlan(
        backend="polars-direct",
        max_workers=min(16, runtime.cpu_count),
        clustering_strategy="in_memory",
        rule_name="plan_selected_fast_box",
    )


rule_fast_box = PlannerRule(
    name="plan_selected_fast_box",
    predicate=_is_fast_box_eligible,
    action=_fast_box_plan,
)


# ── Rule 4: chunked plan (spec §Rule 4) ─────────────────────────────────────

CHUNKED_MIN_PAIRS = SIMPLE_PLAN_MAX_PAIRS  # 50M
CHUNKED_MAX_PAIRS = 5_000_000_000  # 5B
CHUNKED_MIN_RAM_GB = 16.0
CHUNKED_TARGET_RAM_USE_FRACTION = 0.6
_CHUNKED_BYTES_PER_ROW = 1024  # empirical: ~1 KB per row incl. __row_id__ + matchkey


def auto_chunk_size(n_rows_full: int, available_ram_gb: float) -> int:
    """Spec §Rule 4: pick chunk_size targeting ~60% of available RAM.

    Estimated bytes per row is a constant tuning lever; result clamped
    to ``[10_000, 1_000_000]`` per spec range.
    """
    import math

    estimated_gb = (n_rows_full * _CHUNKED_BYTES_PER_ROW) / (1024 ** 3)
    target_chunks = math.ceil(
        estimated_gb / max(available_ram_gb * CHUNKED_TARGET_RAM_USE_FRACTION, 0.001)
    )
    target_chunks = max(target_chunks, 1)
    chunk = n_rows_full // target_chunks
    return max(10_000, min(1_000_000, chunk))


def _is_chunked_eligible(
    profile: ComplexityProfile,
    runtime: RuntimeProfile,
    n_rows_full: int,
) -> bool:
    pairs = profile.blocking.estimated_pair_count
    return (
        pairs >= CHUNKED_MIN_PAIRS
        and pairs < CHUNKED_MAX_PAIRS
        and runtime.available_ram_gb >= CHUNKED_MIN_RAM_GB
    )


def _chunked_plan(
    profile: ComplexityProfile,
    runtime: RuntimeProfile,
    n_rows_full: int,
) -> ExecutionPlan:
    return ExecutionPlan(
        backend="chunked",
        chunk_size=auto_chunk_size(n_rows_full, runtime.available_ram_gb),
        max_workers=min(16, runtime.cpu_count),
        pair_spill_threshold="ram",
        clustering_strategy="in_memory",
        rule_name="plan_selected_chunked",
    )


rule_chunked = PlannerRule(
    name="plan_selected_chunked",
    predicate=_is_chunked_eligible,
    action=_chunked_plan,
)


# ── Rule 7: explicit user override (spec §Rule 7) ───────────────────────────
# Must be FIRST in the registry -- beats every other rule.


def _user_set_backend(
    profile: ComplexityProfile,
    runtime: RuntimeProfile,
    n_rows_full: int,
    context: dict | None,
) -> bool:
    """Spec §Rule 7: fires when context says user set ``config.backend``
    explicitly. Empty / None means no preference; user-set ``polars-direct``
    is technically a no-op vs the default but still counts as an explicit
    preference (don't second-guess the user)."""
    if context is None:
        return False
    return context.get("user_backend") not in (None, "")


def _user_override_plan(
    profile: ComplexityProfile,
    runtime: RuntimeProfile,
    n_rows_full: int,
    context: dict | None,
) -> ExecutionPlan:
    user_backend = (context or {}).get("user_backend", "polars-direct")
    if user_backend == "chunked":
        chunk = auto_chunk_size(n_rows_full, runtime.available_ram_gb)
    else:
        chunk = None
    return ExecutionPlan(
        backend=user_backend,
        chunk_size=chunk,
        max_workers=min(16, runtime.cpu_count),
        clustering_strategy="in_memory",
        rule_name="plan_user_override",
    )


rule_user_override = PlannerRule(
    name="plan_user_override",
    predicate=_user_set_backend,
    action=_user_override_plan,
)


# ── Rule 6: Ray escape hatch (spec §Rule 6) ─────────────────────────────────
# Slotted BEFORE rule_duckdb so ray gets first crack at 50M+; falls through
# to rule_duckdb when ``import ray`` fails (closed-fail behavior per spec).

RAY_MIN_ROWS = 50_000_000


def _has_ray() -> bool:
    """Probe whether ``ray`` is importable. Cheap and side-effect free.

    Uses ``importlib.util.find_spec`` so pyright doesn't complain about
    the optional ``ray`` dep being unresolvable; ``find_spec`` returns
    None when the package isn't installed, without raising.
    """
    import importlib.util

    return importlib.util.find_spec("ray") is not None


def _ray_auto_select_enabled() -> bool:
    """Soft-revert gate (2026-05-18). Distributed Plan v1 (ray + prepared_record_store +
    partitioned_block_scoring) failed the binding 5M kill criterion: treatment RSS
    climbed >> baseline's 7.4 GB peak before completing, while baseline (bucket
    backend) succeeded at 53.7 min / 7.4 GB. Per
    ``project_distributed_plan_v1_kill_criterion`` the auto-pick is gated:
    rule_ray and ``backend="ray"`` still work, but the v3 planner won't pick ray
    automatically unless ``GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1`` is set.
    """
    import os

    return os.environ.get("GOLDENMATCH_ENABLE_DISTRIBUTED_RAY", "0").lower() in (
        "1", "true", "yes",
    )


def _is_ray_eligible(
    profile: ComplexityProfile,
    runtime: RuntimeProfile,
    n_rows_full: int,
) -> bool:
    """Spec §Rule 6: 50M+ rows AND ray import succeeds AND auto-select gate is on.

    Failing closed: when ray isn't installed OR the soft-revert gate isn't set,
    predicate returns False and the planner falls through to rule_duckdb (Rule 5).
    """
    return (
        n_rows_full >= RAY_MIN_ROWS
        and _ray_auto_select_enabled()
        and _has_ray()
    )


def _ray_plan(
    profile: ComplexityProfile,
    runtime: RuntimeProfile,
    n_rows_full: int,
) -> ExecutionPlan:
    return ExecutionPlan(
        backend="ray",
        max_workers=runtime.cpu_count,
        pair_spill_threshold="disk_per_worker",
        clustering_strategy="streaming_cc",
        rule_name="plan_selected_ray",
    )


rule_ray = PlannerRule(
    name="plan_selected_ray",
    predicate=_is_ray_eligible,
    action=_ray_plan,
)


# ── Rule 5: DuckDB out-of-core regime (spec §Rule 5) ────────────────────────

DUCKDB_MIN_PAIRS = 5_000_000_000  # 5B
DUCKDB_MAX_RAM_GB = 16.0  # below this, force DuckDB regardless of pair count
DUCKDB_MAX_WORKERS = 8


def _is_duckdb_eligible(
    profile: ComplexityProfile,
    runtime: RuntimeProfile,
    n_rows_full: int,
) -> bool:
    """Spec §Rule 5: pair_count >= 5B OR available_ram_gb < 16.

    Single-box at-scale plan. Uses DuckDB for the pair store + partitioned
    union-find for clustering (full pair set won't fit in Python memory).
    """
    pairs = profile.blocking.estimated_pair_count
    return (
        pairs >= DUCKDB_MIN_PAIRS
        or runtime.available_ram_gb < DUCKDB_MAX_RAM_GB
    )


def _duckdb_plan(
    profile: ComplexityProfile,
    runtime: RuntimeProfile,
    n_rows_full: int,
) -> ExecutionPlan:
    return ExecutionPlan(
        backend="duckdb",
        max_workers=min(DUCKDB_MAX_WORKERS, runtime.cpu_count),
        pair_spill_threshold="duckdb",
        clustering_strategy="partitioned_union_find",
        rule_name="plan_selected_duckdb",
    )


rule_duckdb = PlannerRule(
    name="plan_selected_duckdb",
    predicate=_is_duckdb_eligible,
    action=_duckdb_plan,
)


# ── Registry ────────────────────────────────────────────────────────────────
#
# Order matters:
#   - rule_user_override MUST be first (beats every other rule).
#   - rule_pathological must precede rule_simple_plan so 1-row inputs hit
#     the defensive path.
#   - rule_ray sits BEFORE rule_duckdb so 50M+ inputs hit ray when it's
#     installed; when ``import ray`` fails, predicate returns False and
#     the planner falls through to rule_duckdb.

DEFAULT_RULES: list[PlannerRule] = [
    rule_user_override,  # MUST be first -- explicit user backend beats all
    rule_pathological,
    rule_simple_plan,
    rule_fast_box,
    rule_chunked,
    rule_ray,            # try first at 50M+; falls through if ray unavailable
    rule_duckdb,         # catch-all for very dense pair counts or low RAM
]
