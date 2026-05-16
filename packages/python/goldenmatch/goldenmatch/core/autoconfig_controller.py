"""AutoConfigController — iterative auto-config with stage-emitted profiles.

Spec: docs/superpowers/specs/2026-05-06-autoconfig-introspective-controller-design.md
      §New: AutoConfigController, §Sample selection, §Pipeline integration.
"""
from __future__ import annotations

import hashlib
import logging
import time
import traceback
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import polars as pl

from goldenmatch.config.schemas import GoldenMatchConfig
from goldenmatch.core.autoconfig_history import RunHistory
from goldenmatch.core.complexity_profile import (
    CollisionSignal,
    ColumnPrior,
    ComplexityProfile,
    DataProfile,
    HealthVerdict,
    SparsityVerdict,
    StopReason,
)

if TYPE_CHECKING:
    from goldenmatch.core.autoconfig_history import HistoryEntry
from goldenmatch.core.autoconfig_memory import AutoConfigMemory, profile_signature
from goldenmatch.core.autoconfig_policy import RefitPolicy

logger = logging.getLogger(__name__)

# Sentinel: forces RED via DataProfile.health() when n_rows == 0.
_RED_PROFILE: ComplexityProfile = ComplexityProfile(data=DataProfile(n_rows=0))

# ContextVar populated at every exit path of AutoConfigController.run()
# (including KeyboardInterrupt) so callers can inspect RunHistory after
# either normal return or exception.  Stores RunHistory directly
# (unlike autoconfig._LAST_CONTROLLER_RUN which stores (profile, history)).
_LAST_CONTROLLER_RUN: ContextVar[RunHistory | None] = ContextVar(
    "_LAST_CONTROLLER_RUN", default=None
)


class IndicatorContext:
    """v1.10: per-run object threading indicators through the policy/rule chain.

    Memoizes lazy indicator calls by (function_name, args). Tracks
    one-shot rule firings for guards like rule_sparse_match_expand.

    Spec: docs/superpowers/specs/2026-05-08-autoconfig-indicators-design.md
          §Architecture #2.

    Fast-mode: when GOLDENMATCH_AUTOCONFIG_INDICATOR_BUDGET=fast, the two
    expensive lazy methods (full_pop_matchkey_hits, cross_blocking_overlap)
    return None unconditionally — preserves v1.9 wall-clock at the cost of
    indicator-driven gains. (Wired in Task 6.1.5; current Phase 3 omits the
    fast-mode guard — Task 6.1.5 adds it.)
    """

    def __init__(
        self,
        df: pl.DataFrame,
        column_priors: dict[str, ColumnPrior],
        sparsity_verdict: SparsityVerdict,
    ) -> None:
        self._df = df
        self._column_priors = column_priors
        self._sparsity_verdict = sparsity_verdict
        self._memo: dict[tuple[Any, ...], Any] = {}
        self._fired: set[str] = set()

    @property
    def column_priors(self) -> dict[str, ColumnPrior]:
        return self._column_priors

    @property
    def sparsity_verdict(self) -> SparsityVerdict:
        return self._sparsity_verdict

    def _is_fast_mode(self) -> bool:
        import os
        return os.environ.get("GOLDENMATCH_AUTOCONFIG_INDICATOR_BUDGET", "").lower() == "fast"

    def full_pop_matchkey_hits(self, blocking_col: str) -> int | None:
        if self._is_fast_mode():
            return None
        from goldenmatch.core.indicators import estimate_full_pop_hits
        key = ("full_pop_matchkey_hits", blocking_col)
        if key not in self._memo:
            self._memo[key] = estimate_full_pop_hits(self._df, blocking_col)
        return self._memo[key]

    def cross_blocking_overlap(self, key_a: str, key_b: str) -> float | None:
        if self._is_fast_mode():
            return None
        from goldenmatch.core.indicators import compute_cross_blocking_overlap
        a, b = sorted([key_a, key_b])
        key = ("cross_blocking_overlap", a, b)
        if key not in self._memo:
            self._memo[key] = compute_cross_blocking_overlap(self._df, a, b)
        return self._memo[key]

    def identity_collision_signal(
        self, identity_col: str, witness_cols: list[str],
    ) -> CollisionSignal:
        if self._is_fast_mode():
            return CollisionSignal(rate=0.0, witness_used="")
        from goldenmatch.core.indicators import compute_identity_collision_signal
        canonical_witnesses = tuple(sorted(witness_cols))
        key = ("identity_collision_signal", identity_col, canonical_witnesses)
        if key not in self._memo:
            self._memo[key] = compute_identity_collision_signal(
                self._df, identity_col, list(canonical_witnesses),
            )
        return self._memo[key]

    def has_fired(self, rule_name: str) -> bool:
        return rule_name in self._fired

    def mark_fired(self, rule_name: str) -> None:
        self._fired.add(rule_name)


def _call_policy_propose(
    policy: Any,
    profile: Any,
    current: Any,
    history: Any,
    ctx: Any,
) -> Any:
    """Call policy.propose with ctx if its signature accepts it; else 3-arg.
    Preserves backward compat for custom policies that pre-date v1.10."""
    import inspect
    params = inspect.signature(policy.propose).parameters
    if "ctx" in params:
        return policy.propose(profile, current, history, ctx=ctx)
    return policy.propose(profile, current, history)


class ConfigValidationError(Exception):
    """Raised when input data is unworkable for ER (empty, all-null, etc.)."""


def _assemble_v0_history_entry(
    sample: pl.DataFrame,
    reference: pl.DataFrame | None,
    config_v0: GoldenMatchConfig,
    history: RunHistory,
    controller: AutoConfigController,
) -> HistoryEntry | None:
    """Build a synthetic HistoryEntry (iteration=-1) for config_v0.

    Strategy:
    1. If history.entries[0].config == config_v0, re-stamp that entry with
       iteration=-1 and decision=None (avoids a second pipeline run when
       iter-0 already profiled v0).
    2. Otherwise, run config_v0 through the pipeline-sample path and wrap
       the result as a new HistoryEntry(iteration=-1).
    3. On any exception: return None (don't crash the controller).
    """
    from goldenmatch.core.autoconfig_history import HistoryEntry
    try:
        # Fast path: iter-0 already profiled config_v0 — re-stamp it.
        if (history.entries
                and history.entries[0].error is None
                and history.entries[0].config == config_v0):
            e0 = history.entries[0]
            return HistoryEntry(
                iteration=-1,
                config=e0.config,
                profile=e0.profile,
                decision=None,
                error=None,
                wall_clock_ms=e0.wall_clock_ms,
            )

        # Slow path: run config_v0 through the sample pipeline.
        import time

        from goldenmatch.core.profile_emitter import profile_capture
        t0 = time.time()
        with profile_capture() as emitter:
            controller._run_pipeline_sample(sample, reference, config_v0)
        profile_v0 = controller._assemble_profile(
            emitter, df=sample, iteration=-1,
            reference=reference, config=config_v0,
        )
        return HistoryEntry(
            iteration=-1,
            config=config_v0,
            profile=profile_v0,
            decision=None,
            error=None,
            wall_clock_ms=int((time.time() - t0) * 1000),
        )
    except Exception as exc:
        logger.warning(
            "auto-config: could not build v0 virtual history entry "
            "(%s: %s); pick_committed will select from regular iterations only — "
            "committed config may be worse than v0",
            type(exc).__name__, exc,
        )
        return None


def _first_red_subprofile(profile: ComplexityProfile) -> str:
    """Return the name of the first sub-profile in canonical declaration
    order whose health() is RED. Used by the WARNING log on commit-RED.

    Canonical order: data, domain, matchkey, blocking, scoring, cluster.
    Returns 'rollup' if no individual sub-profile is RED but the rollup is.
    """
    n_rows = profile.data.n_rows
    checks = [
        ("data", profile.data.health()),
        ("domain", profile.domain.health()),
        ("matchkey", profile.matchkey.health()),
        ("blocking", profile.blocking.health(n_rows=n_rows)),
        ("scoring", profile.scoring.health()),
        ("cluster", profile.cluster.health(n_rows=n_rows)),
    ]
    for name, health in checks:
        if health == HealthVerdict.RED:
            return name
    return "rollup"


@dataclass
class ControllerBudget:
    max_iterations: int = 3
    max_seconds: float = 30.0
    sample_size_default: int = 2000
    sample_skip_below: int = 5000
    converge_epsilon: float = 0.05
    drift_threshold: float = 0.30


class AutoConfigController:
    """Drives iterative refit: pathological-input gates, sampling, policy loop, finalize."""

    def __init__(
        self,
        policy: RefitPolicy,
        budget: ControllerBudget,
        memory: AutoConfigMemory | None = None,
    ) -> None:
        self.policy = policy
        self.budget = budget
        self._memory = memory

    # ---- Public entry point ------------------------------------------------
    def run(
        self,
        df: pl.DataFrame,
        *,
        reference: pl.DataFrame | None = None,
        v0_kwargs: dict | None = None,
        skip_finalize: bool = False,
    ) -> tuple[GoldenMatchConfig, ComplexityProfile, RunHistory]:
        """Run iterative auto-config.

        Returns (committed_config, full_data_profile, history). Pathological
        inputs short-circuit before the loop:
          - empty df → ConfigValidationError("no data to configure on")
          - all-null cols → ConfigValidationError("no usable columns")
          - n_rows == 1 → v0 + YELLOW + history empty
          - 1 user column → v0 + YELLOW + history empty
        """
        # Pathological gates ------------------------------------------------
        if df.height == 0:
            raise ConfigValidationError("no data to configure on")

        user_cols = [c for c in df.columns if not c.startswith("__")]
        if not user_cols:
            raise ConfigValidationError("no usable columns")

        # Check all-null defensively across user columns
        all_null = True
        for col in user_cols:
            if df[col].drop_nulls().len() > 0:
                all_null = False
                break
        if all_null:
            raise ConfigValidationError("no usable columns (all values null)")

        # Single non-empty column or single row → return v0 yellow, skip loop
        if df.height == 1 or len(user_cols) == 1:
            v0 = self._initial_config(df, reference=reference, v0_kwargs=v0_kwargs)
            yellow_profile = self._yellow_sentinel_profile(df.height, user_cols)
            return v0, yellow_profile, RunHistory()

        # Iteration loop (Task 4.2)
        config_v0 = self._initial_config(df, reference=reference, v0_kwargs=v0_kwargs)
        sample, sample_ref = self._take_sample(df, reference=reference)
        history = RunHistory()
        config_n = config_v0
        start = time.time()

        # Task 6.1: eager indicator compute — runs once, before the iteration loop,
        # on the full df (not the sample) so blocking cardinality/overlap signals
        # are representative.
        from goldenmatch.core.indicators import (
            compute_column_priors,
            estimate_sparse_match_signal,
        )
        column_priors = compute_column_priors(df)

        # v1.11: eager NE promotion — runs before the iteration loop so that
        # identity-prior columns (phone, address, etc.) are added as negative
        # evidence on weighted matchkeys before the first iteration profiles them.
        from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence
        config_v0 = promote_negative_evidence(config_v0, df, column_priors)
        config_n = config_v0

        exact_columns: list[str] = []
        for mk in config_v0.get_matchkeys():
            if mk.type == "exact":
                for f in mk.fields:
                    if f.field is not None:
                        exact_columns.append(f.field)
        sparsity_verdict = estimate_sparse_match_signal(df, exact_columns=exact_columns)
        ctx = IndicatorContext(
            df=df,
            column_priors=column_priors,
            sparsity_verdict=sparsity_verdict,
        )

        try:
            for iteration in range(self.budget.max_iterations + 1):
                elapsed = time.time() - start
                if elapsed > self.budget.max_seconds and iteration > 0:
                    history.stop_reason = StopReason.BUDGET_TIME
                    break
                iter_start = time.time()
                try:
                    from goldenmatch.core.profile_emitter import profile_capture
                    with profile_capture() as emitter:
                        self._run_pipeline_sample(sample, sample_ref, config_n)
                    profile_n = self._assemble_profile(
                        emitter, df=sample, iteration=iteration,
                        reference=sample_ref, config=config_n,
                    )
                    wall_ms = int((time.time() - iter_start) * 1000)
                    from goldenmatch.core.autoconfig_history import HistoryEntry
                    history.entries.append(HistoryEntry(
                        iteration=iteration, config=config_n, profile=profile_n,
                        decision=None, error=None, wall_clock_ms=wall_ms,
                    ))
                except KeyboardInterrupt:
                    history.elapsed = timedelta(seconds=time.time() - start)
                    history.stop_reason = StopReason.CANCELLED
                    _LAST_CONTROLLER_RUN.set(history)
                    raise
                except Exception as exc:
                    from goldenmatch.core.autoconfig_history import ErrorRecord, HistoryEntry
                    tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
                    tb_summary = "".join(tb_lines[:5] + tb_lines[-3:])[:2000]
                    history.entries.append(HistoryEntry(
                        iteration=iteration, config=config_n, profile=_RED_PROFILE,
                        decision=None,
                        error=ErrorRecord(
                            exception_type=type(exc).__name__,
                            traceback_summary=tb_summary,
                        ),
                        wall_clock_ms=int((time.time() - iter_start) * 1000),
                    ))
                    continue

                # Stop check
                if profile_n.health() == HealthVerdict.GREEN:
                    # Tier 1a override: even when GREEN, don't exit early on iter 0
                    # if the profile shows a "too tight" blocking signal: perfect precision
                    # (mass_above_threshold == 1.0) with very few candidates relative to
                    # the sample size suggests blocking is over-restricting recall.
                    # Allow the policy to check recall-aware rules before committing.
                    sp = profile_n.scoring
                    bp = profile_n.blocking
                    n_rows = profile_n.data.n_rows
                    _suspicious_tight_blocking = (
                        iteration == 0
                        and sp.mass_above_threshold >= 1.0
                        and sp.candidates_compared > 0
                        and n_rows > 0
                        and sp.candidates_compared < n_rows * 0.5
                        and bp.reduction_ratio > 0.995
                    )
                    if not _suspicious_tight_blocking:
                        history.stop_reason = StopReason.GREEN
                        break
                    # Fall through to policy check — if no rule fires, the policy
                    # returns None and we break naturally below.
                # Convergence guard: only break when the profile is unchanged AND
                # the previous iteration fired no rule (decision=None).  When a rule
                # DID fire but the profile is still the same (e.g. rule_no_matches
                # lowered the threshold but the blocking key still produces the same
                # candidates), we must still call the policy so a follow-up rule
                # (rule_blocking_key_swap) can try a different axis of change.
                if history.profile_distance_to_prev() < self.budget.converge_epsilon:
                    prev_entry = history.entries[-2] if len(history.entries) >= 2 else None
                    if prev_entry is None or prev_entry.decision is None:
                        history.stop_reason = StopReason.CONVERGED
                        break
                if history.is_oscillating():
                    history.stop_reason = StopReason.OSCILLATING
                    break

                # Ask policy for next config (ctx built pre-loop in Task 6.1)
                config_next = _call_policy_propose(self.policy, profile_n, config_n, history, ctx)
                if config_next is None:
                    history.stop_reason = StopReason.POLICY_SATISFIED
                    break
                if config_next == config_n:
                    history.stop_reason = StopReason.POLICY_NO_PROGRESS
                    break
                config_n = config_next
        finally:
            history.elapsed = timedelta(seconds=time.time() - start)

        # Natural loop exhaustion (loop completed without break)
        if history.stop_reason is None:
            history.stop_reason = StopReason.BUDGET_ITERATIONS

        # Append config_v0 as a synthetic virtual entry (iteration=-1) so
        # pick_committed() can fall back to v0 when all real iterations are
        # worse (e.g. the precision-collapse pathology: iter-3 RED with
        # mass_above=1.0 would otherwise beat v0's healthier profile).
        v0_entry = _assemble_v0_history_entry(
            sample, sample_ref, config_v0, history, self,
        )
        if v0_entry is not None:
            history.entries.append(v0_entry)

        # Pick committed config
        best_entry = history.pick_committed(precision_collapse_floor=0.9)
        if best_entry is None:
            # Every iteration errored — no usable profile produced. Fall back to v0.
            n_errored = sum(1 for e in history.entries if e.error is not None)
            n_red = sum(
                1 for e in history.entries
                if e.profile.health() == HealthVerdict.RED and e.error is None
            )
            logger.error(
                "auto-config controller: every iteration errored (n_errored=%d, "
                "n_red=%d, stop_reason=%s); falling back to v0 + RED sentinel. "
                "Inspect _LAST_CONTROLLER_RUN.get() for tracebacks.",
                n_errored,
                n_red,
                history.stop_reason.name if history.stop_reason else "unset",
            )
            _LAST_CONTROLLER_RUN.set(history)
            return config_v0, _RED_PROFILE, history

        # Task 6.1: stamp committed profile with eager column_priors + indicators.
        import dataclasses

        from goldenmatch.core.complexity_profile import IndicatorsProfile

        blocking_col: str | None = None
        if best_entry.config.blocking and best_entry.config.blocking.keys:
            _bk = best_entry.config.blocking.keys[0]
            if _bk.fields:
                blocking_col = _bk.fields[0]

        full_pop_hits = (
            ctx._memo.get(("full_pop_matchkey_hits", blocking_col))
            if blocking_col else None
        )
        cross_blocking_overlap_val: float | None = None
        for _mk, _mv in ctx._memo.items():
            if _mk[0] == "cross_blocking_overlap":
                cross_blocking_overlap_val = _mv
                break

        _stamped_data = dataclasses.replace(
            best_entry.profile.data,
            column_priors=column_priors,
        )
        _stamped_indicators = IndicatorsProfile(
            full_pop_matchkey_hit_rate=(
                float(full_pop_hits) if full_pop_hits is not None else None
            ),
            cross_blocking_overlap=cross_blocking_overlap_val,
        )
        _stamped_profile = dataclasses.replace(
            best_entry.profile,
            data=_stamped_data,
            indicators=_stamped_indicators,
        )
        best_entry = dataclasses.replace(best_entry, profile=_stamped_profile)
        if best_entry.iteration >= 0 and best_entry.iteration < len(history.entries):
            history.entries[best_entry.iteration] = best_entry
        elif best_entry.iteration < 0:
            # v0 virtual entry (iteration=-1) lives at the END of
            # history.entries (appended above after the iteration loop).
            # Find it by attribute and replace in-place so downstream
            # consumers of history.pick_committed() see the column_priors-
            # stamped profile. Without this, a v0 commit (PR #197 fix for
            # issue #195) leaves an unstamped profile in history and any
            # caller that re-runs pick_committed() gets a profile with no
            # column_priors / no indicators populated.
            for _i, _e in enumerate(history.entries):
                if _e.iteration == best_entry.iteration:
                    history.entries[_i] = best_entry
                    break

        committed_health = best_entry.profile.health()
        iter_label = "v0" if best_entry.iteration == -1 else str(best_entry.iteration)
        if committed_health == HealthVerdict.RED:
            failing = _first_red_subprofile(best_entry.profile)
            logger.warning(
                "auto-config committed best-effort RED config "
                "(iter=%s, stop_reason=%s, failing_subprofile=%s); "
                "downstream pipeline will run but output may be low-precision",
                iter_label,
                history.stop_reason.name if history.stop_reason else "unset",
                failing,
            )
        elif committed_health == HealthVerdict.YELLOW:
            logger.info(
                "auto-config committed YELLOW config "
                "(iter=%s, stop_reason=%s)",
                iter_label,
                history.stop_reason.name if history.stop_reason else "unset",
            )
        # health == GREEN: silent success

        # Post-iteration: decorate committed config with LLM scorer if appropriate.
        # This runs ONCE, outside the iteration loop, so it never competes with
        # structural rules for the iteration budget.
        committed_config = self._maybe_decorate_with_llm_scorer(
            best_entry.config, best_entry.profile,
        )

        # ── Controller v3 planner (phase 2): pick execution plan based on the
        # committed profile + runtime introspection. Phase 2 lands with an
        # empty rule list (no behavior change); phases 3-6 register rules.
        from goldenmatch.core.autoconfig_planner import apply_planner_rules
        from goldenmatch.core.runtime_profile import capture_runtime_profile

        runtime = capture_runtime_profile()
        # Extrapolate the committed (sample) blocking profile to full-row count
        # so the planner rules in later phases see signals at full scale.
        committed_profile = best_entry.profile
        if committed_profile.meta.is_sample and committed_profile.meta.sample_size > 0:
            blocking_full = committed_profile.blocking.extrapolate_to(
                n_rows_sample=committed_profile.meta.sample_size,
                n_rows_full=df.height,
            )
            profile_for_planner = dataclasses.replace(committed_profile, blocking=blocking_full)
        else:
            profile_for_planner = committed_profile

        plan = apply_planner_rules(
            profile=profile_for_planner,
            runtime=runtime,
            n_rows_full=df.height,
            rules=[],  # phases 3-6 register rules
        )
        plan.apply_to(committed_config)
        history.execution_plan = plan

        # Fix 4: When skip_finalize=True (called from _api zero-config path),
        # skip the full-data _finalize run. The caller will execute the real
        # full pipeline immediately after, so running it here would be a double
        # full run. Drift detection is deferred to Task 6.1 when the caller can
        # compare pf.signals (a typed ComplexityProfile) against best_entry.profile.
        if skip_finalize:
            # Return the best sample profile in lieu of a full-data profile.
            # history.full_vs_sample_drift is left None (drift not computed).
            self._record_run(df, reference, best_entry, history)
            _LAST_CONTROLLER_RUN.set(history)
            return committed_config, best_entry.profile, history

        # Finalize on full data (Task 4.3)
        profile_full = self._finalize(committed_config, df, reference)
        # Stamp full-data profile with eager column_priors + indicators too.
        _full_stamped_data = dataclasses.replace(
            profile_full.data,
            column_priors=column_priors,
        )
        profile_full = dataclasses.replace(
            profile_full,
            data=_full_stamped_data,
            indicators=_stamped_indicators,
        )
        # Drift detection
        sample_vec = best_entry.profile.normalized_signal_vector()
        full_vec = profile_full.normalized_signal_vector()
        drift = sum(abs(a - b) for a, b in zip(sample_vec, full_vec))
        history.full_vs_sample_drift = drift

        self._record_run(df, reference, best_entry, history)
        _LAST_CONTROLLER_RUN.set(history)
        return committed_config, profile_full, history

    # ---- Internals --------------------------------------------------------
    def _initial_config(
        self,
        df: pl.DataFrame,
        *,
        reference: pl.DataFrame | None,
        v0_kwargs: dict | None = None,
    ) -> GoldenMatchConfig:
        """Return the starting config for the controller iteration loop.

        Consults cross-run memory first: if a previous successful run with the
        same data-shape signature exists, return that cached config directly
        (skipping the legacy heuristic).  Falls back to the legacy heuristic
        when no memory hit is found.

        ``v0_kwargs`` are threaded through to ``_legacy_auto_configure_v0`` so
        that callers of ``auto_configure_df(strict=True, llm_auto=True, ...)``
        have their kwargs honoured in the initial heuristic pass.

        Today's heuristic lives in core/autoconfig.py::auto_configure_df.
        Task 5.1 will refactor that function to call the controller; until
        then, we re-import and call it (it does not yet recurse via the
        controller).
        """
        # Tier 4: consult cross-run memory before falling back to legacy heuristic
        if self._memory is not None:
            mode = "match" if reference is not None else "dedupe"
            sig = profile_signature(df, mode=mode)
            cached = self._memory.lookup_best(sig)
            if cached is not None:
                logger.info(
                    "auto-config: using cached config from prior run for signature %s",
                    sig,
                )
                return cached

        # Late import to avoid circulars
        from goldenmatch.core.autoconfig import _legacy_auto_configure_v0 as _legacy

        kw = v0_kwargs or {}

        # The legacy function does not (yet) accept a `reference` kwarg; for
        # match-mode we call it on the concatenated frame as a v0 stand-in.
        # Task 5.1 will plumb reference through properly; this is a
        # placeholder that produces a workable starting config.
        if reference is not None:
            try:
                merged = pl.concat([df, reference], how="vertical_relaxed")
            except Exception:
                merged = df
            return _legacy(merged, **kw)
        return _legacy(df, **kw)

    def _take_sample(
        self, df: pl.DataFrame, *, reference: pl.DataFrame | None
    ) -> tuple[pl.DataFrame, pl.DataFrame | None]:
        """Uniform random sample with a deterministic seed derived from data shape.

        Below ``sample_skip_below`` rows → returns full data unchanged.
        Above that threshold → samples exactly ``sample_size_default`` rows.
        Seed = hash of (n_rows, column names) for reproducibility across runs
        with the same schema.

        Match mode samples target and reference independently with the same rules.

        # TODO(autoconfig-v2): replace with stratified sampling (e.g. by blocking-key
        # group) to ensure rare subgroups are represented in the sample and the
        # profile more faithfully reflects full-data behaviour.
        """
        target_sample = self._sample_one(df)
        ref_sample = self._sample_one(reference) if reference is not None else None
        return target_sample, ref_sample

    def _yellow_sentinel_profile(
        self, n_rows: int, user_cols: list[str]
    ) -> ComplexityProfile:
        """Build a ComplexityProfile that rolls up to YELLOW.

        Used for the pathological short-circuit paths (single-row, single-column)
        where no pipeline run occurred. Sub-profiles are set to their minimum
        non-RED values so the rollup is driven by the DataProfile verdict (YELLOW
        for n_cols==1 or uniform column types).
        """
        from goldenmatch.core.complexity_profile import (
            BlockingProfile,
            ScoringProfile,
        )
        # BlockingProfile: avoid RED by faking one block with good reduction
        blocking = BlockingProfile(
            n_blocks=max(n_rows, 1),
            total_comparisons=max(n_rows, 1),
            reduction_ratio=0.9,
            block_sizes_p50=1,
            block_sizes_p95=1,
            block_sizes_p99=1,
            block_sizes_max=1,
            singleton_block_count=0,
            oversized_block_count=0,
        )
        # ScoringProfile: avoid RED by providing non-zero mass/dip.
        # Set candidates_compared=1 as a nominal sentinel so ScoringProfile.health()
        # doesn't return RED on the "candidates_compared==0" guard.  The actual
        # scoring didn't run for these pathological-input short-circuit paths.
        scoring = ScoringProfile(
            n_pairs_scored=0,
            candidates_compared=1,
            dip_statistic=0.01,
            mass_above_threshold=0.01,
            mass_in_borderline=0.0,
        )
        return ComplexityProfile(
            data=DataProfile(
                n_rows=n_rows, n_cols=len(user_cols),
                column_types={c: "unknown" for c in user_cols},
            ),
            blocking=blocking,
            scoring=scoring,
        )

    def _sample_one(self, df: pl.DataFrame) -> pl.DataFrame:
        if df.height < self.budget.sample_skip_below:
            return df
        seed = self._seed_for(df)
        n = min(self.budget.sample_size_default, df.height)
        # Polars sample with shuffle=False keeps row order; we want diverse coverage so shuffle=True.
        return df.sample(n=n, seed=seed, shuffle=True)

    def _seed_for(self, df: pl.DataFrame) -> int:
        """Hash of data shape for reproducible sampling."""
        key = f"{df.height}|{','.join(df.columns)}".encode()
        digest = hashlib.sha256(key).hexdigest()
        return int(digest[:8], 16)

    def _run_pipeline_sample(
        self,
        sample: pl.DataFrame,
        reference: pl.DataFrame | None,
        config: GoldenMatchConfig,
    ) -> None:
        """Run the lightweight pipeline (blocking → score → cluster) on the sample.

        Uses the public ``dedupe_df`` / ``match_df`` API so the same instrumented
        stages run; the active ``profile_capture()`` collects sub-profiles.
        """
        from goldenmatch._api import dedupe_df, match_df
        if reference is None:
            dedupe_df(sample, config=config)
        else:
            match_df(sample, reference, config=config)

    def _compute_recall_probe(
        self,
        sample: pl.DataFrame,
        config: GoldenMatchConfig,
        *,
        n_samples: int = 100,
    ) -> float | None:
        """Score N random pairs (i, j with i != j, uniform from the sample) using
        the first weighted matchkey's scoring logic. Returns the fraction whose
        weighted score is >= the matchkey's threshold.

        A high rate (e.g. > 0.05) suggests the blocking is excluding real matches:
        if random non-blocked pairs match, the blocking key is over-restricting.

        Returns None when no weighted matchkey is configured or the sample is
        too small to probe meaningfully.
        """
        import random

        from goldenmatch.core.scorer import score_field
        from goldenmatch.utils.transforms import apply_transforms

        weighted_mk = next(
            (mk for mk in (config.matchkeys or []) if mk.type == "weighted"), None,
        )
        if weighted_mk is None:
            return None
        if sample.height < 4:
            return None

        threshold = weighted_mk.threshold or 0.7
        rng = random.Random(self._seed_for(sample))
        n_rows = sample.height
        rows = sample.to_dicts()

        above = 0
        total = 0
        attempts = 0
        target = min(n_samples, n_rows * (n_rows - 1) // 2)
        while total < target and attempts < target * 4:
            attempts += 1
            i = rng.randrange(n_rows)
            j = rng.randrange(n_rows)
            if i == j:
                continue
            weighted_sum = 0.0
            weight_sum = 0.0
            for f in (weighted_mk.fields or []):
                if f.scorer is None or f.field is None:
                    continue
                # Map composite/compound scorers to a single-pair-friendly fallback.
                # 'ensemble' is a block-level scorer (not supported by score_field);
                # use jaro_winkler as a close single-field proxy for the probe.
                _probe_scorer = f.scorer
                if _probe_scorer == "ensemble":
                    _probe_scorer = "jaro_winkler"
                val_i = apply_transforms(rows[i].get(f.field), f.transforms or [])
                val_j = apply_transforms(rows[j].get(f.field), f.transforms or [])
                try:
                    s = score_field(val_i, val_j, _probe_scorer)
                except (ValueError, Exception):
                    continue
                if s is None:
                    continue
                w = f.weight or 1.0
                weighted_sum += s * w
                weight_sum += w
            if weight_sum > 0:
                score = weighted_sum / weight_sum
                total += 1
                if score >= threshold:
                    above += 1
        return above / total if total else None

    def _assemble_profile(
        self,
        emitter: Any,
        *,
        df: pl.DataFrame,
        iteration: int,
        is_sample: bool = True,
        reference: pl.DataFrame | None = None,
        config: GoldenMatchConfig | None = None,
    ) -> ComplexityProfile:
        """Build ComplexityProfile from emitter writes. Missing sub-profiles
        fall back to defaults computed from ``df`` (plus ``reference`` in match
        mode) so the rollup gets a real DataProfile.

        In match mode the DataProfile must reflect the *combined* target +
        reference frame because BlockingProfile is built over the combined
        frame.  Passing only the target sample causes ``n_rows`` to be ~half
        the true count, making ``rule_blocking_too_coarse``'s average block
        size calculation use the wrong denominator (Bug A).
        """
        import dataclasses

        from goldenmatch.core.complexity_profile import (
            BlockingProfile,
            ClusterProfile,
            DomainProfile,
            MatchkeyProfile,
            ProfileMeta,
            ScoringProfile,
        )

        data = emitter.data or self._compute_data_profile(df, reference=reference)
        scoring = emitter.scoring or ScoringProfile()

        # Tier 1a: compute random-pair recall probe (only on real iterations, not the
        # finalize sentinel iteration=-1) and when a config is available.
        if iteration >= 0 and config is not None:
            probe_rate = self._compute_recall_probe(df, config)
            scoring = dataclasses.replace(scoring, random_pair_above_threshold_rate=probe_rate)

        return ComplexityProfile(
            data=data,
            domain=emitter.domain or DomainProfile(),
            matchkey=emitter.matchkey or MatchkeyProfile(),
            blocking=emitter.blocking or BlockingProfile(),
            scoring=scoring,
            cluster=emitter.cluster or ClusterProfile(),
            meta=ProfileMeta(
                iteration=iteration, is_sample=is_sample,
                sample_size=df.height, n_rows_full=df.height,
            ),
        )

    def _compute_data_profile(
        self, df: pl.DataFrame, reference: pl.DataFrame | None = None
    ) -> DataProfile:
        """Compute a real DataProfile from a DataFrame. Used as fallback when
        no pipeline stage emitted one (sample iterations don't go through
        the autoconfig column-profiling step).

        In match mode (``reference`` provided) the combined target+reference
        frame is used for statistics so that ``n_rows`` reflects the total
        record count seen by the blocking stage, not just the target half.
        If concatenation fails due to schema mismatch, falls back to target-only
        with a warning.
        """
        from goldenmatch.core.complexity_profile import DataProfile

        if reference is not None:
            try:
                df = pl.concat([df, reference], how="vertical_relaxed")
            except Exception as exc:
                logger.warning(
                    "match-mode n_rows fallback: concat target+reference failed (%s); "
                    "DataProfile will reflect target only",
                    exc,
                )

        user_cols = [c for c in df.columns if not c.startswith("__")]
        n_rows = df.height

        column_types: dict[str, str] = {}
        cardinality_ratio: dict[str, float] = {}
        null_rate: dict[str, float] = {}
        value_length_p50: dict[str, int] = {}
        value_length_p99: dict[str, int] = {}

        for col in user_cols:
            ser = df[col]
            non_null = ser.drop_nulls()
            n_non_null = non_null.len()
            cardinality_ratio[col] = (non_null.n_unique() / n_non_null) if n_non_null else 0.0
            null_rate[col] = 1 - (n_non_null / n_rows) if n_rows else 0.0
            dtype = str(ser.dtype).lower()
            if "utf" in dtype or "str" in dtype:
                column_types[col] = "text"
            elif "int" in dtype or "float" in dtype:
                column_types[col] = "numeric"
            elif "date" in dtype or "time" in dtype:
                column_types[col] = "date"
            else:
                column_types[col] = "unknown"
            if column_types[col] == "text" and n_non_null:
                try:
                    lens = sorted(non_null.cast(pl.Utf8).str.len_chars().to_list())
                    if lens:
                        value_length_p50[col] = int(lens[len(lens) // 2])
                        value_length_p99[col] = int(lens[max(0, int(0.99 * len(lens)) - 1)])
                except Exception:
                    pass

        return DataProfile(
            n_rows=n_rows,
            n_cols=len(user_cols),
            column_types=column_types,  # pyright: ignore[reportArgumentType]  # runtime values match ColumnType literal set

            cardinality_ratio=cardinality_ratio,
            null_rate=null_rate,
            value_length_p50=value_length_p50,
            value_length_p99=value_length_p99,
        )

    def _finalize(
        self,
        config: GoldenMatchConfig,
        df: pl.DataFrame,
        reference: pl.DataFrame | None,
    ) -> ComplexityProfile:
        """Run the full pipeline on the full data with profile capture. Returns
        a ComplexityProfile reflecting actual full-data behavior. Drift vs the
        final sample profile is computed in run() and recorded on history.
        """
        from goldenmatch.core.profile_emitter import profile_capture
        with profile_capture() as emitter:
            self._run_pipeline_sample(df, reference, config)
        return self._assemble_profile(
            emitter, df=df, iteration=-1, is_sample=False,
            reference=reference, config=None,
        )

    def _maybe_decorate_with_llm_scorer(
        self,
        config: GoldenMatchConfig,
        profile: ComplexityProfile,
    ) -> GoldenMatchConfig:
        """If the committed profile is borderline-heavy AND an LLM API key is
        available, enable LLMScorerConfig on the committed config so the
        user-facing pipeline run uses per-pair LLM scoring on borderline pairs.

        This runs ONCE after the controller iteration loop commits, not inside
        the loop — so LLM decoration never competes with structural rules for
        the iteration budget. On DQbench, structural rules dominate every
        iteration; the old rule_enable_llm_scorer (position 10 in DEFAULT_RULES)
        never got a turn.

        Bounds (candidate_lo / candidate_hi / auto_threshold) track the weighted
        matchkey's threshold dynamically (Change B): with controller-lowered
        thresholds (~0.5 typical), the hardcoded 0.60–0.90 band was entirely
        above the score distribution and no LLM calls fired.
        """
        from goldenmatch.core.autoconfig_rules import _llm_api_key_available
        sp = profile.scoring
        if sp.candidates_compared == 0:
            return config
        if sp.mass_in_borderline < 0.10:
            return config
        if not _llm_api_key_available():
            return config
        if config.llm_scorer is not None and config.llm_scorer.enabled:
            return config

        # Find the weighted matchkey threshold
        weighted_mk = next(
            (mk for mk in (config.matchkeys or []) if mk.type == "weighted"),
            None,
        )
        if weighted_mk is None or weighted_mk.threshold is None:
            return config
        threshold = float(weighted_mk.threshold)

        # Adaptive bounds (Change 2, 2026-05-07):
        # When most of the above-threshold mass is borderline-confident (>0.5),
        # the LLM needs to inspect those high-scoring pairs to filter false
        # positives. Drop auto_threshold near 1.0 so the LLM sees them all.
        # Otherwise keep the standard band centered on the matchkey threshold.
        if sp.mass_in_borderline > 0.5:
            # Wide-open mode: LLM inspects almost everything above threshold.
            candidate_lo = max(0.0, threshold - 0.05)
            candidate_hi = 0.99    # very narrow auto-accept zone
            auto_threshold = 0.99
            mode = "wide"
        else:
            # Standard mode: straddle the threshold with more headroom above
            # (where pairs are more likely to be true matches worth verifying).
            candidate_lo = max(0.0, threshold - 0.10)
            candidate_hi = min(1.0, threshold + 0.20)
            auto_threshold = candidate_hi
            mode = "standard"

        from goldenmatch.config.schemas import BudgetConfig, LLMScorerConfig
        new_cfg = config.model_copy(update={
            "llm_scorer": LLMScorerConfig(
                enabled=True,
                candidate_lo=candidate_lo,
                candidate_hi=candidate_hi,
                auto_threshold=auto_threshold,
                budget=BudgetConfig(max_calls=500, max_cost_usd=1.0),
            ),
        })
        logger.info(
            "auto-config: enabling LLMScorerConfig (mode=%s) "
            "(mass_in_borderline=%.3f, threshold=%.2f, "
            "candidate_lo=%.2f, candidate_hi=%.2f, auto_threshold=%.2f)",
            mode, sp.mass_in_borderline, threshold,
            candidate_lo, candidate_hi, auto_threshold,
        )
        return new_cfg

    def _record_run(
        self,
        df: pl.DataFrame,
        reference: pl.DataFrame | None,
        best_entry: Any,
        history: RunHistory,
    ) -> None:
        """Persist the committed config to memory (if memory is configured).

        Only records when ``best_entry`` is not None (i.e. at least one
        healthy iteration completed). Only ``succeeded=True`` runs are
        retrievable via ``lookup_best``, so failed runs are stored but
        invisible to the cache lookup.
        """
        if self._memory is None or best_entry is None:
            return
        mode = "match" if reference is not None else "dedupe"
        sig = profile_signature(df, mode=mode)
        sp = best_entry.profile.scoring
        f1_proxy: float | None = None
        if sp.candidates_compared > 0:
            f1_proxy = sp.mass_above_threshold * (1.0 - sp.mass_in_borderline)
        succeeded = best_entry.profile.health() != HealthVerdict.RED
        try:
            self._memory.remember(
                sig,
                best_entry.config,
                succeeded=succeeded,
                n_iterations=history.iteration,
                f1_proxy=f1_proxy,
            )
            logger.debug(
                "auto-config: recorded run to memory (sig=%s, succeeded=%s, f1_proxy=%s)",
                sig, succeeded, f1_proxy,
            )
        except Exception as exc:
            logger.warning("auto-config: failed to record run to memory: %s", exc)
