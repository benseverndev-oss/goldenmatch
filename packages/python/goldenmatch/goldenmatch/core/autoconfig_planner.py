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
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from goldenmatch.core.complexity_profile import ComplexityProfile
from goldenmatch.core.execution_plan import ExecutionPlan
from goldenmatch.core.runtime_profile import RuntimeProfile

# Variadic so both 3-arg (profile, runtime, n_rows_full) and 4-arg
# (..., context) callables type-check. The dispatcher introspects each
# rule's signature and threads context only when accepted.
Predicate = Callable[..., bool]
Action = Callable[..., ExecutionPlan]


def _accepts_context(fn: Callable[..., Any]) -> bool:
    """True if ``fn`` declares a ``context`` parameter.

    Cached on the function via ``__planner_accepts_context__`` so the
    inspect.signature call only runs once per rule.
    """
    cached = getattr(fn, "__planner_accepts_context__", None)
    if cached is not None:
        return cached
    try:
        params = inspect.signature(fn).parameters
        accepts = "context" in params
    except (TypeError, ValueError):
        accepts = False
    try:
        fn.__planner_accepts_context__ = accepts  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass
    return accepts


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
    context: dict[str, Any] | None = None,
) -> ExecutionPlan:
    """Walk the rule list in order; first match's action returns the plan.

    Args:
        profile: ``ComplexityProfile`` from the last controller iteration,
            extrapolated to ``n_rows_full`` if the controller iterated
            on a sample.
        runtime: ``RuntimeProfile`` captured at controller-start.
        n_rows_full: Total row count of the caller's input DataFrame.
        rules: The rule registry. Empty -> default plan.
        context: Optional dict of side-channel signals (e.g.
            ``{"user_backend": "ray"}``). Rules declare a ``context``
            parameter to receive it; rules without that parameter are
            called with the legacy 3-arg signature (phase-2 unit tests
            stay green).

    Returns:
        ``ExecutionPlan`` with ``rule_name`` set to either the matched
        rule's name or a sentinel (``no_rules_registered``,
        ``no_rule_matched``).
    """
    if not rules:
        return ExecutionPlan(rule_name="no_rules_registered")

    for rule in rules:
        if _accepts_context(rule.predicate):
            fired = rule.predicate(profile, runtime, n_rows_full, context=context)
        else:
            fired = rule.predicate(profile, runtime, n_rows_full)
        if not fired:
            continue
        if _accepts_context(rule.action):
            plan = rule.action(profile, runtime, n_rows_full, context=context)
        else:
            plan = rule.action(profile, runtime, n_rows_full)
        if plan.rule_name is None:
            plan = dataclasses.replace(plan, rule_name=rule.name)
        return plan

    return ExecutionPlan(rule_name="no_rule_matched")
