"""Concrete planner rules for the goldenpipe auto-config brain (slice 1).

Ordered; first match wins (see plan_pipeline). All predicates read only cheap
PipeProfile signals. Portable — no Polars/Pydantic.
"""
from __future__ import annotations

from goldenpipe.autoconfig_planner import (
    PipePlan,
    PipePlannerRule,
    PipeProfile,
    PlannedStage,
    default_evidence,
)

_CONFIDENT_DOMAIN_THRESHOLD = 0.5


def _is_pathological(p: PipeProfile) -> bool:
    return p.n_rows <= 1


def _pathological_plan(p: PipeProfile) -> PipePlan:
    return PipePlan(
        stages=(
            PlannedStage("goldencheck.scan", {}),
            PlannedStage("goldenflow.transform", {}),
        ),
        rule_name="pathological",
        confidence=1.0,
        evidence=default_evidence(p),
    )


rule_pathological = PipePlannerRule("pathological", _is_pathological, _pathological_plan)


def _is_confident_schema(p: PipeProfile) -> bool:
    return p.inferred_domain is not None and p.domain_confidence >= _CONFIDENT_DOMAIN_THRESHOLD


def _confident_schema_plan(p: PipeProfile) -> PipePlan:
    return PipePlan(
        stages=(
            PlannedStage("infer_schema", {"domain": p.inferred_domain}),
            PlannedStage("goldencheck.scan", {}),
            PlannedStage("goldenflow.transform", {}),
            PlannedStage("goldenmatch.dedupe", {}),
        ),
        rule_name="confident_schema",
        confidence=p.domain_confidence,
        evidence=default_evidence(p),
    )


rule_confident_schema = PipePlannerRule(
    "confident_schema", _is_confident_schema, _confident_schema_plan,
)


DEFAULT_RULES: tuple[PipePlannerRule, ...] = (
    rule_pathological,
    rule_confident_schema,
)
