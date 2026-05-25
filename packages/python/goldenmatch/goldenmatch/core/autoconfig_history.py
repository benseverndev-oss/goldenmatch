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
from goldenmatch.core.execution_plan import ExecutionPlan


@dataclass
class PolicyDecision:
    """Audit-trail record of one rule firing.

    #125: ``expand_sample`` is an out-of-band controller signal — when
    set to a positive factor, the controller resamples ``df`` with
    ``sample_cap *= factor`` before the next iteration. Used by
    ``rule_sparse_match_expand`` to actually grow the sample
    (replacing the v1.10 lower-threshold proxy). Default ``None`` =
    no expansion requested.
    """
    rule_name: str
    rationale: str
    config_diff: dict[str, Any]
    expand_sample: float | None = None


@dataclass
class ErrorRecord:
    """Captured exception from a sample iteration that crashed."""
    exception_type: str
    traceback_summary: str


@dataclass
class HistoryEntry:
    """One iteration's record in the controller's audit trail.

    Invariant: ``error`` is the sole discriminant for success vs failure.

    - **Success path:** ``error is None`` and ``profile`` is a real
      ``ComplexityProfile`` (the iteration's measurement).
    - **Failure path:** ``error`` is set (an ``ErrorRecord``) and
      ``profile`` is the module-level ``_RED_PROFILE`` sentinel
      (defined in ``autoconfig_controller.py``) — never ``None``, but
      also not a real measurement. The sentinel is used only so that
      type-checkers see ``profile: ComplexityProfile`` rather than
      ``ComplexityProfile | None``.

    The controller's iteration loop and ``_assemble_v0_history_entry``
    (the post-loop virtual-v0 append site) both maintain this invariant.

    ``RunHistory.pick_committed()``'s filter is
    ``e.error is None and e.profile is not None``. The ``profile is not
    None`` clause is defensive — under the invariant it's always true on
    the success path — but guards against accidental misuse if a future
    code path ever passes ``profile=None``.
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
    execution_plan: ExecutionPlan | None = None

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

    def pick_committed(
        self,
        precision_collapse_floor: float | None = None,
        use_zero_label_confidence: bool = False,
    ) -> HistoryEntry | None:
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

        ``precision_collapse_floor`` (added v1.9 amendment): when set, RED
        entries whose ``profile.scoring.mass_above_threshold`` exceeds the
        floor are demoted in the lex key (rank=3 instead of 2). This
        protects against the "everything matches" pathology. Typical
        value: 0.9. Disabled by default to preserve backward compat.

        ``use_zero_label_confidence`` (zero-label Phase 2, env-gated via
        ``GOLDENMATCH_AUTOCONFIG_ZERO_LABEL_COMMIT``): when True and an entry's
        profile carries a ``zero_label`` profile, the secondary tiebreaker
        becomes ``-zero_label.overall_confidence`` instead of ``-mass_separation``.
        This commits the config whose unlabeled ER structure is most plausible
        rather than the one that merely inflates ``mass_above_threshold`` (the
        bias ``-mass_separation`` has). ``overall_confidence`` already bakes in
        the everything-matches / no-matches / cluster-collapse guards, so this
        is defense-in-depth alongside ``precision_collapse_floor`` (which still
        applies). Default False -> identical behavior to before (no change).
        """
        if (precision_collapse_floor is not None
                and not (0.0 <= precision_collapse_floor <= 1.0)):
            raise ValueError(
                f"precision_collapse_floor must be in [0, 1]; got "
                f"{precision_collapse_floor!r}"
            )
        survivors = [
            e for e in self.entries
            if e.error is None and e.profile is not None
        ]
        if not survivors:
            return None

        def key(e: HistoryEntry) -> tuple[int, float, int]:
            verdict = e.profile.health()
            rank = {
                HealthVerdict.GREEN: 0,
                HealthVerdict.YELLOW: 1,
                HealthVerdict.RED: 2,
            }[verdict]
            sp = e.profile.scoring
            sep = sp.mass_above_threshold - sp.mass_in_borderline
            if (precision_collapse_floor is not None
                    and verdict == HealthVerdict.RED
                    and sp.mass_above_threshold > precision_collapse_floor):
                # Precision-collapsed regime ("everything matches"). Within
                # this regime, `-sep` is mechanically biased toward lower
                # thresholds: a lower threshold narrows the borderline band,
                # which mechanically increases sep without the model actually
                # separating anything better. Tiebreaking on -sep then makes
                # the controller commit whichever iteration lowered the
                # threshold most, even when nothing's being merged on the
                # real data.
                #
                # Fix: in the collapsed regime, neutralise sep (set tiebreaker
                # to 0.0) so the lex order falls through to iteration. v0
                # (iteration=-1) wins among collapsed candidates, which is
                # the right "safest fallback" behaviour — at worst we commit
                # the user's input config and emit a warning, instead of a
                # threshold-lowered variant that produces a degenerate
                # dedupe on the real data.
                #
                # See issue #195 / scale-audit 2M-degeneration for the bug
                # this addresses: at 2M, low_transitivity fired 3x lowering
                # threshold 0.80 -> 0.65; precision_collapse_floor demoted
                # all entries to rank=3; the pre-fix tiebreaker picked the
                # most-lowered iteration; downstream pipeline produced 2,570
                # clusters vs the ~145K v0 would have produced.
                rank = 3
                return (rank, 0.0, e.iteration)
            # Zero-label Phase 2: prefer the most-plausible unlabeled structure
            # over the -sep heuristic (which is biased toward lower thresholds).
            if use_zero_label_confidence and e.profile.zero_label is not None:
                return (rank, -e.profile.zero_label.overall_confidence, e.iteration)
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
        return self.pick_committed()  # no precision_collapse_floor — preserve deprecated behavior
