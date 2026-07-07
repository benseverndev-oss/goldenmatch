"""Concrete planner rules for the goldenpipe auto-config brain.

Ordered; first match wins (see plan_pipeline). Predicates read PlannerInput
(runtime + complexity). Portable — no Polars/Pydantic. Stage names are the EXACT
dotted registry names (plan_to_config drops any name not in the registry).
"""
from __future__ import annotations

from goldenpipe.autoconfig_planner import (
    PipePlan,
    PipePlannerRule,
    PlannedStage,
    PlannerInput,
    default_evidence,
)

_CONFIDENT_DOMAIN_THRESHOLD = 0.5
RED_NULL_DENSITY = 0.6


def _is_pathological(inp: PlannerInput) -> bool:
    return inp.runtime.n_rows <= 1


def _pathological_plan(inp: PlannerInput) -> PipePlan:
    return PipePlan(
        stages=(
            PlannedStage("goldencheck.scan", {}),
            PlannedStage("goldenflow.transform", {}),
        ),
        rule_name="pathological",
        confidence=1.0,
        evidence=default_evidence(inp),
    )


rule_pathological = PipePlannerRule("pathological", _is_pathological, _pathological_plan)


def _is_confident_schema(inp: PlannerInput) -> bool:
    r = inp.runtime
    return r.inferred_domain is not None and r.domain_confidence >= _CONFIDENT_DOMAIN_THRESHOLD


def _confident_schema_plan(inp: PlannerInput) -> PipePlan:
    return PipePlan(
        stages=(
            PlannedStage("infer_schema", {"domain": inp.runtime.inferred_domain}),
            PlannedStage("goldencheck.scan", {}),
            PlannedStage("goldenflow.transform", {}),
            PlannedStage("goldenmatch.dedupe", {}),
        ),
        rule_name="confident_schema",
        confidence=inp.runtime.domain_confidence,
        evidence=default_evidence(inp),
    )


rule_confident_schema = PipePlannerRule(
    "confident_schema", _is_confident_schema, _confident_schema_plan,
)


def _is_low_confidence(inp: PlannerInput) -> bool:
    # The sole RED source: no usable signal AND the data is mostly empty, so
    # running the full dedupe pipeline is likely to produce garbage clusters.
    return (
        inp.runtime.inferred_domain is None
        and inp.complexity.max_null_density > RED_NULL_DENSITY
    )


def _low_confidence_plan(inp: PlannerInput) -> PipePlan:
    # Return the SAFE DEFAULT shape (so small inputs proceed) but tag it RED so
    # the glue can refuse at scale.
    return PipePlan(
        stages=(
            PlannedStage("goldencheck.scan", {}),
            PlannedStage("goldenflow.transform", {}),
            PlannedStage("goldenmatch.dedupe", {}),
        ),
        rule_name="low_confidence",
        confidence=0.3,
        evidence=default_evidence(inp),
    )


rule_low_confidence = PipePlannerRule(
    "low_confidence", _is_low_confidence, _low_confidence_plan,
)


# Order: positive rules first (a clear signal wins), then low_confidence (RED),
# then plan_pipeline's plain default. Nothing shadows low_confidence — rules 1/2
# require n_rows<=1 / domain-present, which the garbage case (domain None, high
# null) does not satisfy.
DEFAULT_RULES: tuple[PipePlannerRule, ...] = (
    rule_pathological,
    rule_confident_schema,
    rule_low_confidence,
)
