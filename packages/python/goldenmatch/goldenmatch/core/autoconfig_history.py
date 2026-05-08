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
    """One iteration's record in the controller's audit trail.

    Invariant: exactly one of ``error`` and ``profile`` is non-None,
    never both, never neither. The controller's iteration-loop append
    site enforces this — every code path either records a real profile
    (success) or records an ``ErrorRecord`` paired with the
    ``_RED_PROFILE_SENTINEL`` (failure path treats sentinel as the
    profile slot for type compatibility, but the entry's ``error`` is
    set, indicating no real profile was produced).

    ``RunHistory.pick_committed()``'s filter relies on this invariant
    (``error is None and profile is not None``); the invariant is
    documented but not defensively re-checked at the filter site.
    """
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

    def pick_committed(self) -> HistoryEntry | None:
        """Pick the entry to commit. Returns None ONLY if every entry
        errored or has no profile — otherwise returns the best entry by
        lexicographic key, where RED entries are last resort but still
        beat 'no commit at all.'

        Replaces ``cheapest_healthy()`` as of v1.9 — the new behavior
        commits a best-effort entry whenever any iteration produced a
        usable profile, even if that profile is RED. The user-visible
        health verdict on the returned entry tells them what they got.

        Lex key: ``(health_rank, -mass_separation, iteration)`` where
        ``health_rank`` is 0/1/2 for GREEN/YELLOW/RED and
        ``mass_separation = mass_above_threshold - mass_in_borderline``.

        Filter: ``e.error is None and e.profile is not None`` (per the
        ``HistoryEntry`` invariant — guards the sentinel-mismatch case
        defensively).
        """
        survivors = [
            e for e in self.entries
            if e.error is None and e.profile is not None
        ]
        if not survivors:
            return None

        def key(e: HistoryEntry) -> tuple[int, float, int]:
            h = e.profile.health()
            rank = {
                HealthVerdict.GREEN: 0,
                HealthVerdict.YELLOW: 1,
                HealthVerdict.RED: 2,
            }[h]
            sp = e.profile.scoring
            sep = sp.mass_above_threshold - sp.mass_in_borderline
            return (rank, -sep, e.iteration)

        return min(survivors, key=key)

    def cheapest_healthy(self) -> HistoryEntry | None:
        """**DEPRECATED**: use ``pick_committed()`` instead.

        Behavior change in v1.9: this alias delegates to ``pick_committed()``,
        which returns RED entries when no GREEN/YELLOW exists (instead of
        returning None as in v1.8). Update callers that depended on the
        v1.8 None-on-all-RED behavior to either:
        * call ``pick_committed()`` and check the returned entry's
          ``.profile.health()`` to handle RED explicitly, or
        * inspect ``.health() != HealthVerdict.RED`` on the result.

        Removed in v2.0.
        """
        import warnings
        warnings.warn(
            "RunHistory.cheapest_healthy() is deprecated; use pick_committed(). "
            "Behavior change: pick_committed() returns RED entries when no "
            "GREEN/YELLOW exists (cheapest_healthy() returned None in v1.8).",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.pick_committed()
