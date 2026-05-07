"""RefitPolicy protocol + HeuristicRefitPolicy dispatcher.

The actual rule list (5 rules) lives in ``autoconfig_rules.py`` and is loaded
lazily so this module can be tested in isolation. Rules are pure functions:

    Rule = Callable[
        [ComplexityProfile, GoldenMatchConfig, RunHistory],
        Optional[tuple[GoldenMatchConfig, PolicyDecision]],
    ]

Spec: docs/superpowers/specs/2026-05-06-autoconfig-introspective-controller-design.md
      §HeuristicRefitPolicy rule table (v1).
"""
from __future__ import annotations
from typing import Any, Callable, Protocol

from goldenmatch.core.complexity_profile import ComplexityProfile, HealthVerdict
from goldenmatch.core.autoconfig_history import RunHistory, PolicyDecision

# Rule type alias. Returns (new_config, decision) tuple if it fires, else None.
Rule = Callable[
    [ComplexityProfile, Any, RunHistory],
    "tuple[Any, PolicyDecision] | None",
]


class RefitPolicy(Protocol):
    def propose(
        self,
        profile: ComplexityProfile,
        current: Any,
        history: RunHistory,
    ) -> Any | None: ...


class HeuristicRefitPolicy:
    """Ordered rule table. First rule that returns non-None wins.

    Per spec §RefitPolicy.propose return semantics (S1-A):
      - None = satisfied → controller breaks loop with POLICY_SATISFIED
      - A new config that == current is also treated as satisfied (bug guard)
      - A previously-seen config is allowed; oscillation handled by controller
    """

    def __init__(self, rules: list[Rule] | None = None) -> None:
        if rules is None:
            from goldenmatch.core.autoconfig_rules import DEFAULT_RULES
            rules = DEFAULT_RULES
        self._rules: list[Rule] = rules

    def propose(
        self,
        profile: ComplexityProfile,
        current: Any,
        history: RunHistory,
    ) -> Any | None:
        if profile.health() == HealthVerdict.GREEN:
            return None
        for rule in self._rules:
            outcome = rule(profile, current, history)
            if outcome is None:
                continue
            new_config, decision = outcome
            if new_config == current:
                # Bug guard: rule "decided to do nothing" without saying so.
                # Logged at WARN by the controller (not here — keep policy pure).
                return None
            # Attach decision to the latest history entry, if any
            if history.entries:
                history.entries[-1].decision = decision
            return new_config
        # No rule fired → satisfied
        return None
