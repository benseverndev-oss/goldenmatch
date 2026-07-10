"""Plan-first auto-config decision core (portable — NO Polars/Pydantic).

The pyo3-free-portable kernel: PlannerInput (in) -> PipePlan (out) via a pure
rule table. Host glue (Polars profiling, Pydantic config, refuse-raise) lives in
`autoconfig_glue.py`. Mirrors goldenmatch's controller (PlannerRule + first-match
plan, RuntimeProfile + ComplexityProfile, traffic-light confidence) so the later
`goldenpipe-core` Rust port is mechanical.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class PipeProfile:
    """Cheap, up-front signals — no stage execution required."""
    n_rows: int
    n_cols: int
    column_names: tuple[str, ...]
    dtypes: tuple[str, ...]
    inferred_domain: str | None
    domain_confidence: float


@dataclass(frozen=True)
class ComplexityProfile:
    """Data-derived signals from one columnar pass. Zeros = unknown
    (engine-resident frame not profiled this slice)."""
    max_null_density: float    # 0..1, worst column's null fraction
    mean_null_density: float   # 0..1, mean across columns


@dataclass(frozen=True)
class PlannerInput:
    """Everything a rule sees: cheap runtime signals + deeper complexity."""
    runtime: PipeProfile
    complexity: ComplexityProfile


@dataclass(frozen=True)
class PlannedStage:
    name: str            # EXACT registry name (e.g. "goldencheck.scan", "infer_schema")
    config: dict         # per-stage config


@dataclass(frozen=True)
class PipePlan:
    stages: tuple[PlannedStage, ...]
    rule_name: str
    confidence: float
    evidence: dict


Predicate = Callable[["PlannerInput"], bool]
Action = Callable[["PlannerInput"], "PipePlan"]


@dataclass(frozen=True)
class PipePlannerRule:
    rule_name: str
    predicate: Predicate
    action: Action


GREEN_THRESHOLD = 0.7
AMBER_THRESHOLD = 0.4


def band_of(confidence: float) -> str:
    """Map a confidence float to a traffic-light band (Rust-portable strings)."""
    if confidence >= GREEN_THRESHOLD:
        return "green"
    if confidence >= AMBER_THRESHOLD:
        return "amber"
    return "red"


def default_evidence(inp: PlannerInput) -> dict:
    """Signal snapshot attached to every plan (evidence for humans/telemetry)."""
    return {
        "n_rows": inp.runtime.n_rows,
        "n_cols": inp.runtime.n_cols,
        "inferred_domain": inp.runtime.inferred_domain,
        "domain_confidence": inp.runtime.domain_confidence,
        "max_null_density": inp.complexity.max_null_density,
        "mean_null_density": inp.complexity.mean_null_density,
    }


def _default_plan(inp: PlannerInput) -> PipePlan:
    """The current static shape: scan -> flow -> dedupe (no infer_schema)."""
    return PipePlan(
        stages=(
            PlannedStage("goldencheck.scan", {}),
            PlannedStage("goldenflow.transform", {}),
            PlannedStage("goldenmatch.dedupe", {}),
        ),
        rule_name="default",
        confidence=0.7,
        evidence=default_evidence(inp),
    )


def plan_pipeline(
    inp: PlannerInput,
    rules: Sequence[PipePlannerRule] | None = None,
) -> PipePlan:
    """First matching rule's action builds the plan; else the default shape."""
    if rules is None:
        from goldenpipe.autoconfig_planner_rules import DEFAULT_RULES
        rules = DEFAULT_RULES
    for rule in rules:
        if rule.predicate(inp):
            return rule.action(inp)
    return _default_plan(inp)


SCALE_ROUTE_MIN_ROWS = 1_000_000
_THROUGHPUT_RECALL_TARGET = 0.95


def apply_scale_hints(plan: PipePlan, runtime: PipeProfile) -> PipePlan:
    """Composable post-transform: at/above SCALE_ROUTE_MIN_ROWS, attach a
    throughput hint to the dedupe stage so GoldenMatch routes to its
    sketch-then-verify tier. No-op below the threshold or when the plan has no
    dedupe stage. Pure — returns a new PipePlan, never mutates the input.

    The hint travels as a reserved ``_dedupe_hints`` key in the dedupe stage's
    config; the match.py adapter recognizes it and forwards it to
    ``dedupe_df(throughput=...)`` (auto-config + hint) rather than treating it as
    a full-config override.
    """
    if runtime.n_rows < SCALE_ROUTE_MIN_ROWS:
        return plan
    if not any(s.name == "goldenmatch.dedupe" for s in plan.stages):
        return plan
    new_stages = tuple(
        PlannedStage(
            s.name,
            {**s.config, "_dedupe_hints": {"throughput": {"recall_target": _THROUGHPUT_RECALL_TARGET}}},
        )
        if s.name == "goldenmatch.dedupe"
        else s
        for s in plan.stages
    )
    return PipePlan(
        stages=new_stages,
        rule_name=plan.rule_name,
        confidence=plan.confidence,
        evidence={**plan.evidence, "scale_hinted": True},
    )
