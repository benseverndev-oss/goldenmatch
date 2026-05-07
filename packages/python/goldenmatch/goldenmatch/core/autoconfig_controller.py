"""AutoConfigController — iterative auto-config with stage-emitted profiles.

Spec: docs/superpowers/specs/2026-05-06-autoconfig-introspective-controller-design.md
      §New: AutoConfigController, §Sample selection, §Pipeline integration.

Task 4.1 implements: skeleton, ControllerBudget, StopReason, _RED_PROFILE,
pathological-input gates, _take_sample, and a stub run() that handles the
gates and returns v0 without entering the loop.

The iteration loop body lands in Task 4.2 and _finalize in Task 4.3.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Any
import hashlib
import logging
import time
import traceback
import polars as pl

from goldenmatch.config.schemas import GoldenMatchConfig
from goldenmatch.core.complexity_profile import (
    ComplexityProfile, DataProfile, HealthVerdict,
)
from goldenmatch.core.autoconfig_history import RunHistory
from goldenmatch.core.autoconfig_policy import RefitPolicy


logger = logging.getLogger(__name__)

# Sentinel: forces RED via DataProfile.health() when n_rows == 0.
_RED_PROFILE: ComplexityProfile = ComplexityProfile(data=DataProfile(n_rows=0))


class ConfigValidationError(Exception):
    """Raised when input data is unworkable for ER (empty, all-null, etc.)."""


class StopReason(Enum):
    GREEN = "green"
    CONVERGED = "converged"
    BUDGET_ITERATIONS = "budget_iterations"
    BUDGET_TIME = "budget_time"
    POLICY_SATISFIED = "policy_satisfied"
    POLICY_NO_PROGRESS = "policy_no_progress"
    OSCILLATING = "oscillating"
    CANCELLED = "cancelled"


@dataclass
class ControllerBudget:
    max_iterations: int = 3
    max_seconds: float = 30.0
    sample_size_default: int = 2000
    sample_skip_below: int = 5000
    converge_epsilon: float = 0.05
    drift_threshold: float = 0.30


class AutoConfigController:
    """Drives iterative refit. Task 4.1 supplies skeleton + gates + sampling.

    The actual iteration loop is in Task 4.2; finalize is in Task 4.3.
    """

    def __init__(self, policy: RefitPolicy, budget: ControllerBudget) -> None:
        self.policy = policy
        self.budget = budget

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

        try:
            for iteration in range(self.budget.max_iterations + 1):
                elapsed = time.time() - start
                if elapsed > self.budget.max_seconds and iteration > 0:
                    break
                iter_start = time.time()
                try:
                    from goldenmatch.core.profile_emitter import profile_capture
                    with profile_capture() as emitter:
                        self._run_pipeline_sample(sample, sample_ref, config_n)
                    profile_n = self._assemble_profile(
                        emitter, df=sample, iteration=iteration,
                    )
                    wall_ms = int((time.time() - iter_start) * 1000)
                    from goldenmatch.core.autoconfig_history import HistoryEntry
                    history.entries.append(HistoryEntry(
                        iteration=iteration, config=config_n, profile=profile_n,
                        decision=None, error=None, wall_clock_ms=wall_ms,
                    ))
                except KeyboardInterrupt:
                    history.elapsed = timedelta(seconds=time.time() - start)
                    raise
                except Exception as exc:
                    from goldenmatch.core.autoconfig_history import HistoryEntry, ErrorRecord
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
                    break
                if history.profile_distance_to_prev() < self.budget.converge_epsilon:
                    break
                if history.is_oscillating():
                    break

                # Ask policy for next config
                config_next = self.policy.propose(profile_n, config_n, history)
                if config_next is None:
                    break
                if config_next == config_n:
                    break
                config_n = config_next
        finally:
            history.elapsed = timedelta(seconds=time.time() - start)

        # Pick committed config
        best_entry = history.cheapest_healthy()
        if best_entry is None:
            # No healthy iterations → return v0 + RED sentinel.
            # Fix 6: emit a warning so operators can diagnose.
            n_crashed = len(history.errors)
            n_red = sum(
                1 for e in history.entries
                if e.profile.health() == HealthVerdict.RED and e.error is None
            )
            logger.warning(
                "auto-config controller could not produce a healthy config; "
                "committing v0. Iterations: %d total, %d crashed, %d RED. "
                "Inspect _LAST_CONTROLLER_RUN.get() for diagnostics.",
                history.iteration, n_crashed, n_red,
            )
            return config_v0, _RED_PROFILE, history

        # Fix 4: When skip_finalize=True (called from _api zero-config path),
        # skip the full-data _finalize run. The caller will execute the real
        # full pipeline immediately after, so running it here would be a double
        # full run. Drift detection is deferred to Task 6.1 when the caller can
        # compare pf.signals (a typed ComplexityProfile) against best_entry.profile.
        if skip_finalize:
            # Return the best sample profile in lieu of a full-data profile.
            # history.full_vs_sample_drift is left None (drift not computed).
            return best_entry.config, best_entry.profile, history

        # Finalize on full data (Task 4.3)
        profile_full = self._finalize(best_entry.config, df, reference)
        # Drift detection
        sample_vec = best_entry.profile.normalized_signal_vector()
        full_vec = profile_full.normalized_signal_vector()
        drift = sum(abs(a - b) for a, b in zip(sample_vec, full_vec))
        history.full_vs_sample_drift = drift

        return best_entry.config, profile_full, history

    # ---- Internals --------------------------------------------------------
    def _initial_config(
        self,
        df: pl.DataFrame,
        *,
        reference: pl.DataFrame | None,
        v0_kwargs: dict | None = None,
    ) -> GoldenMatchConfig:
        """Run the legacy heuristic to produce config v0.

        ``v0_kwargs`` are threaded through to ``_legacy_auto_configure_v0`` so
        that callers of ``auto_configure_df(strict=True, llm_auto=True, ...)``
        have their kwargs honoured in the initial heuristic pass.

        Today's heuristic lives in core/autoconfig.py::auto_configure_df.
        Task 5.1 will refactor that function to call the controller; until
        then, we re-import and call it (it does not yet recurse via the
        controller).
        """
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
            BlockingProfile, ScoringProfile, ClusterProfile,
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
        # ScoringProfile: avoid RED by providing non-zero mass/dip
        scoring = ScoringProfile(
            n_pairs_scored=0,
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
        key = f"{df.height}|{','.join(df.columns)}".encode("utf-8")
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

    def _assemble_profile(
        self,
        emitter: Any,
        *,
        df: pl.DataFrame,
        iteration: int,
        is_sample: bool = True,
    ) -> ComplexityProfile:
        """Build ComplexityProfile from emitter writes. Missing sub-profiles
        fall back to defaults computed from ``df`` so the rollup gets a
        real DataProfile (without it, n_rows=0 forces RED and the controller
        can never converge on a healthy config)."""
        from goldenmatch.core.complexity_profile import (
            DataProfile, BlockingProfile, ScoringProfile, ClusterProfile,
            MatchkeyProfile, DomainProfile, ProfileMeta,
        )

        data = emitter.data or self._compute_data_profile(df)

        return ComplexityProfile(
            data=data,
            domain=emitter.domain or DomainProfile(),
            matchkey=emitter.matchkey or MatchkeyProfile(),
            blocking=emitter.blocking or BlockingProfile(),
            scoring=emitter.scoring or ScoringProfile(),
            cluster=emitter.cluster or ClusterProfile(),
            meta=ProfileMeta(
                iteration=iteration, is_sample=is_sample,
                sample_size=df.height, n_rows_full=df.height,
            ),
        )

    def _compute_data_profile(self, df: pl.DataFrame) -> "DataProfile":
        """Compute a real DataProfile from a DataFrame. Used as fallback when
        no pipeline stage emitted one (sample iterations don't go through
        the autoconfig column-profiling step)."""
        from goldenmatch.core.complexity_profile import DataProfile

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
            column_types=column_types,
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
        )
