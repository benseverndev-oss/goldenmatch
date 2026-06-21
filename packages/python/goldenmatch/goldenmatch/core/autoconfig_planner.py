"""Planner-rule dispatcher for controller v3.

Spec Â§Decision rules:
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

from goldenmatch.core._native_loader import native_enabled, native_module
from goldenmatch.core.complexity_profile import ComplexityProfile
from goldenmatch.core.execution_plan import ExecutionPlan
from goldenmatch.core.runtime_profile import RuntimeProfile


def _default_rules() -> list[PlannerRule]:
    """Lazily return DEFAULT_RULES from autoconfig_planner_rules.

    Imported here (not at module top) to break the potential import cycle:
    autoconfig_planner_rules imports PlannerRule from this module, so a
    top-level ``from autoconfig_planner_rules import DEFAULT_RULES`` here
    would create a cycle.
    """
    from goldenmatch.core.autoconfig_planner_rules import DEFAULT_RULES  # noqa: PLC0415
    return DEFAULT_RULES

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
        predicate: Returns True when this rule applies. Spec Â§Rule N
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
    # Native path: only for the production DEFAULT_RULES registry and when the
    # "autoconfig" component is enabled. Custom rule lists (used in unit tests)
    # stay on the pure-Python path. As of the source-of-truth cutover (Task F1,
    # 2026-06-21) "autoconfig" IS in _GATED_ON, so under GOLDENMATCH_NATIVE=auto
    # this runs whenever the ext carries the symbol -- output is byte-identical
    # to pure Python (golden-vector parity), and the `hasattr` guard below falls
    # back to pure Python on a wheel that predates the symbol.
    if native_enabled("autoconfig"):
        _nm = native_module()
        if hasattr(_nm, "autoconfig_decide_plan") and rules is _default_rules():
            from goldenmatch.core.autoconfig_native import (  # noqa: PLC0415
                build_planner_capabilities,
                plan_from_json,
                plan_input_to_json,
            )
            caps = build_planner_capabilities(context)
            out = _nm.autoconfig_decide_plan(
                plan_input_to_json(profile, runtime, n_rows_full, caps)
            )
            return plan_from_json(out)

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


def apply_throughput_overlay(plan, cfg, *, metric, signature_len):
    """Overlay sketch-then-verify posture onto a base ExecutionPlan (orthogonal to
    backend selection). metric in {"jaccard","cosine"}; signature_len = num_perms
    (lexical) or num_planes (semantic)."""
    from goldenmatch.core.throughput_verify import DEFAULT_SIMILARITY, select_banding
    similarity = cfg.similarity_threshold or DEFAULT_SIMILARITY[metric]
    bands, rows = select_banding(metric, signature_len, similarity, cfg.recall_target)
    return dataclasses.replace(plan, verify_mode="sketch_distance",
                               sketch_bands=bands, sketch_rows=rows, sketch_similarity=similarity,
                               sketch_metric=metric)