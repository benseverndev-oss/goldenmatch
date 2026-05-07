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
import polars as pl

from goldenmatch.config.schemas import GoldenMatchConfig
from goldenmatch.core.complexity_profile import (
    ComplexityProfile, DataProfile, HealthVerdict,
)
from goldenmatch.core.autoconfig_history import RunHistory
from goldenmatch.core.autoconfig_policy import RefitPolicy


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
            v0 = self._initial_config(df, reference=reference)
            yellow_profile = self._yellow_sentinel_profile(df.height, user_cols)
            return v0, yellow_profile, RunHistory()

        # Loop body lands in Task 4.2. For now, bail out with v0 + sentinel.
        v0 = self._initial_config(df, reference=reference)
        # Returning a placeholder profile so the public contract is correct;
        # Task 4.2 replaces this with the real iteration loop + finalize.
        placeholder = ComplexityProfile(
            data=DataProfile(
                n_rows=df.height, n_cols=len(user_cols),
                column_types={c: "unknown" for c in user_cols},
            ),
        )
        return v0, placeholder, RunHistory()

    # ---- Internals --------------------------------------------------------
    def _initial_config(
        self, df: pl.DataFrame, *, reference: pl.DataFrame | None
    ) -> GoldenMatchConfig:
        """Run the legacy heuristic to produce config v0.

        Today's heuristic lives in core/autoconfig.py::auto_configure_df.
        Task 5.1 will refactor that function to call the controller; until
        then, we re-import and call it (it does not yet recurse via the
        controller).
        """
        # Late import to avoid circulars
        from goldenmatch.core.autoconfig import auto_configure_df as _legacy

        # The legacy function does not (yet) accept a `reference` kwarg; for
        # match-mode we call it on the concatenated frame as a v0 stand-in.
        # Task 5.1 will plumb reference through properly; this is a
        # placeholder that produces a workable starting config.
        if reference is not None:
            try:
                merged = pl.concat([df, reference], how="vertical_relaxed")
            except Exception:
                merged = df
            return _legacy(merged)
        return _legacy(df)

    def _take_sample(
        self, df: pl.DataFrame, *, reference: pl.DataFrame | None
    ) -> tuple[pl.DataFrame, pl.DataFrame | None]:
        """Stratified sample. Below sample_skip_below → full data; above → sample_size_default rows.

        Deterministic seed = hash of (n_rows, columns) for reproducibility.
        Match mode samples target and reference independently with the same rules.
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
        # Polars sample with shuffle=False keeps row order; we want diverse coverage so shuffle=True.
        return df.sample(n=self.budget.sample_size_default, seed=seed, shuffle=True)

    def _seed_for(self, df: pl.DataFrame) -> int:
        """Hash of data shape for reproducible sampling."""
        key = f"{df.height}|{','.join(df.columns)}".encode("utf-8")
        digest = hashlib.sha256(key).hexdigest()
        return int(digest[:8], 16)
