"""RunHistory + audit-trail dataclasses for AutoConfigController.

Spec: docs/superpowers/specs/2026-05-06-autoconfig-introspective-controller-design.md
      §Types & contracts § "RunHistory".
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from goldenmatch.core.complexity_profile import ComplexityProfile, HealthVerdict, StopReason


@dataclass
class PolicyDecision:
    """Audit-trail record of one rule firing."""
    rule_name: str
    rationale: str
    config_diff: dict[str, Any]


@dataclass
class ErrorRecord:
    """Captured exception from a sample iteration that crashed."""
    exception_type: str
    traceback_summary: str


@dataclass
class HistoryEntry:
    iteration: int
    config: Any                # GoldenMatchConfig at runtime; Any to avoid import cycle
    profile: ComplexityProfile
    decision: PolicyDecision | None
    error: ErrorRecord | None
    wall_clock_ms: int


@dataclass
class RunHistory:
    entries: list[HistoryEntry] = field(default_factory=list)
    full_vs_sample_drift: float | None = None
    elapsed: timedelta = field(default_factory=lambda: timedelta(0))
    prior_runs: list[Any] = field(default_factory=list)  # v2 hook for Learning Memory
    stop_reason: StopReason | None = None

    @property
    def iteration(self) -> int:
        return len(self.entries)

    @property
    def decisions(self) -> list[PolicyDecision]:
        return [e.decision for e in self.entries if e.decision is not None]

    @property
    def errors(self) -> list[ErrorRecord]:
        return [e.error for e in self.entries if e.error is not None]

    def is_oscillating(self) -> bool:
        """Same (config_hash, decision_hash) pair appears ≥2× in last 4 iters."""
        window = self.entries[-4:]
        if len(window) < 4:
            return False
        sigs = []
        for e in window:
            cfg_h = hash(repr(e.config))
            dec_h = hash(e.decision.rule_name) if e.decision else 0
            sigs.append((cfg_h, dec_h))
        return any(c >= 2 for c in Counter(sigs).values())

    def profile_distance_to_prev(self) -> float:
        """L1 distance between last two profiles' normalized signal vectors.
        Returns +inf when fewer than 2 entries (no prior to compare to)."""
        if len(self.entries) < 2:
            return float("inf")
        a = self.entries[-1].profile.normalized_signal_vector()
        b = self.entries[-2].profile.normalized_signal_vector()
        return sum(abs(x - y) for x, y in zip(a, b))

    def cheapest_healthy(self) -> HistoryEntry | None:
        """Spec §Types & contracts § Cheapest-healthy ordering (S1-B):
        lex key = (health_rank, -mass_separation, iteration).

        Returns None when no entry has health != RED.
        """
        survivors = [e for e in self.entries if e.profile.health() != HealthVerdict.RED]
        if not survivors:
            return None

        def key(e: HistoryEntry) -> tuple[int, float, int]:
            health_rank = 0 if e.profile.health() == HealthVerdict.GREEN else 1
            sep = e.profile.scoring.mass_above_threshold - e.profile.scoring.mass_in_borderline
            return (health_rank, -sep, e.iteration)

        return min(survivors, key=key)
