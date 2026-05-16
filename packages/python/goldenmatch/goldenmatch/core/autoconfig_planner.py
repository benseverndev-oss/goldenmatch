"""Planner-rule dispatcher for controller v3.

Spec §Decision rules:
docs/superpowers/specs/2026-05-15-controller-v3-planner-design.md.

Rules are (name, predicate, action) tuples. ``apply_planner_rules``
evaluates them in registry order; the first predicate to return True
fires, its action returns an ``ExecutionPlan``. Default plan returned
when no rule matches (polars-direct; rule_name='no_rules_registered'
for empty registry, 'no_rule_matched' otherwise).

Phases 3-6 register rules; this module just dispatches.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Callable

from goldenmatch.core.complexity_profile import ComplexityProfile
from goldenmatch.core.execution_plan import ExecutionPlan
from goldenmatch.core.runtime_profile import RuntimeProfile

Predicate = Callable[..., bool]
Action = Callable[..., ExecutionPlan]


@dataclass(frozen=True)
class PlannerRule:
    """One entry in the planner's rule table.

    Attributes:
        name: Stable identifier surfaced in ``RunHistory.decisions``.
        predicate: Returns True when this rule applies. Spec §Rule N
            tables document each rule's predicate.
        action: Builds the ``ExecutionPlan`` when ``predicate`` is True.
    """

    name: str
    predicate: Predicate
    action: Action


def apply_planner_rules(
    profile: ComplexityProfile,
    runtime: RuntimeProfile,
    n_rows_full: int,
    rules: list[PlannerRule],
) -> ExecutionPlan:
    """Walk the rule list in order; first match's action returns the plan.

    Args:
        profile: ``ComplexityProfile`` from the last controller iteration,
            extrapolated to ``n_rows_full`` if the controller iterated
            on a sample.
        runtime: ``RuntimeProfile`` captured at controller-start.
        n_rows_full: Total row count of the caller's input DataFrame.
        rules: The rule registry. Empty -> default plan. Phases 3-6 populate.

    Returns:
        ``ExecutionPlan`` with ``rule_name`` set to either the matched
        rule's name or a sentinel (``no_rules_registered``,
        ``no_rule_matched``).
    """
    if not rules:
        return ExecutionPlan(rule_name="no_rules_registered")

    for rule in rules:
        if rule.predicate(profile, runtime, n_rows_full):
            plan = rule.action(profile, runtime, n_rows_full)
            if plan.rule_name is None:
                plan = dataclasses.replace(plan, rule_name=rule.name)
            return plan

    return ExecutionPlan(rule_name="no_rule_matched")
