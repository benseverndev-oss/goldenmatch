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


# ── Registry ────────────────────────────────────────────────────────────────
#
# Phase 3 ships these two rules. Order matters: pathological must fire
# before the simple plan, otherwise the simple plan would absorb
# 1-row inputs. Phases 4-6 append fast-box, chunked, DuckDB, Ray, and
# the user-override rule (which moves to position [0]).

DEFAULT_RULES: list[PlannerRule] = [
    rule_pathological,
    rule_simple_plan,
]
