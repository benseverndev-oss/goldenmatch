"""AutoConfigController — iterative auto-config with stage-emitted profiles.

Spec: docs/superpowers/specs/2026-05-06-autoconfig-introspective-controller-design.md
      §New: AutoConfigController, §Sample selection, §Pipeline integration.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
import traceback
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
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
    from goldenmatch.distributed.record_store import PreparedRecordStore
from goldenmatch.core.autoconfig_memory import AutoConfigMemory, profile_signature
from goldenmatch.core.autoconfig_policy import RefitPolicy

logger = logging.getLogger(__name__)

# Sentinel: forces RED via DataProfile.health() when n_rows == 0.
_RED_PROFILE: ComplexityProfile = ComplexityProfile(data=DataProfile(n_rows=0))

# Confidence gate threshold (Phase 3 of controller-budget-pathology spec).
# Back-projected from the 500K -> ~26 min measured wall on synthetic
# surname fixtures: committing a RED config on a large input produces
# degenerate full-data dedupe. 100K is the safe upper bound below which
# the pipeline still completes in a tolerable wall-clock window.
REFUSE_AT_N: int = 100_000

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


# #131: stratified-sampling helpers. Mid-cardinality column picker +
# Polars-native stratified sampler. Both module-level so tests can
# unit-test them without spinning up a full AutoConfigController.

# Stratification-key cardinality band. Below MIN, too few distinct
# values to stratify usefully; above MAX, too many strata (every
# stratum gets ~1 row, no signal).
_STRAT_MIN_DISTINCT = 10
_STRAT_MAX_DISTINCT = 500

# Column-name regex preference: blocking-shaped columns make better
# strat keys than arbitrary mid-card columns. Used as a tiebreak.
_STRAT_NAME_PREFERENCE = (
    "zip", "postal", "state", "region", "country", "city",
    "phone_area", "area_code",
)


def _pick_stratification_key(df: pl.DataFrame) -> str | None:
    """Pick the most-informative mid-cardinality column for stratification.

    Picks columns with 10–500 distinct values AND ratio < 0.5 (so
    strata aren't singletons). Prefers blocking-shaped names (zip,
    state, etc) as tiebreak. Returns None when no column qualifies —
    caller falls back to uniform random.
    """
    n_rows = df.height
    if n_rows <= 0:
        return None
    candidates: list[tuple[str, int, int]] = []  # (name, distinct, preference_rank)
    for col in df.columns:
        if col.startswith("__"):  # skip internal bookkeeping
            continue
        try:
            n_distinct = int(df[col].n_unique())
        except Exception:  # pragma: no cover -- defensive
            continue
        if not (_STRAT_MIN_DISTINCT <= n_distinct <= _STRAT_MAX_DISTINCT):
            continue
        # Skip columns where most rows are unique. partition_by on a
        # near-unique column produces N singleton strata; the inner loop's
        # `alloc >= stratum.height` branch returns all of them, defeating
        # the sample cap.
        if n_distinct / n_rows > 0.5:
            continue
        # Lower preference_rank = better match. -1 = name matched preferences.
        rank = -1 if any(p in col.lower() for p in _STRAT_NAME_PREFERENCE) else 0
        candidates.append((col, n_distinct, rank))
    if not candidates:
        return None
    # Sort: preference-rank ASC (preferred names first), then by
    # distinct count DESC (more strata = finer stratification, up to cap).
    candidates.sort(key=lambda x: (x[2], -x[1]))
    return candidates[0][0]


def _stratified_sample(
    df: pl.DataFrame,
    strat_key: str,
    target_n: int,
    *,
    min_per_stratum: int = 10,
    seed: int = 0,
) -> pl.DataFrame:
    """Sample ``target_n`` rows stratified by ``strat_key``.

    Allocates rows proportionally to stratum size, with a minimum
    floor (``min_per_stratum``) so rare strata get representation
    even when proportional allocation would round them to 0. If the
    sum of floors exceeds target_n (very many small strata), each
    stratum gets ``target_n // n_strata`` rows.
    """
    strata = df.partition_by(strat_key, as_dict=False)
    n_strata = len(strata)
    if n_strata == 0:
        return df.sample(n=min(target_n, df.height), seed=seed, shuffle=True)
    # Proportional allocation with floor.
    total = df.height
    allocations: list[int] = []
    for stratum in strata:
        proportional = max(int(stratum.height * target_n / total), min_per_stratum)
        allocations.append(min(proportional, stratum.height))
    # If the floor blew the budget, fall back to even allocation.
    if sum(allocations) > target_n * 1.5:
        per_stratum = max(target_n // n_strata, 1)
        allocations = [min(per_stratum, s.height) for s in strata]
    samples = []
    for stratum, alloc in zip(strata, allocations):
        if alloc >= stratum.height:
            samples.append(stratum)
        elif alloc > 0:
            samples.append(stratum.sample(n=alloc, seed=seed, shuffle=True))
    return pl.concat(samples) if samples else df.head(target_n)


class ConfigValidationError(Exception):
    """Raised when input data is unworkable for ER (empty, all-null, etc.)."""


class ControllerNotConfidentError(Exception):
    """Raised when AutoConfigController committed a RED-health config on
    a large input (df.height >= REFUSE_AT_N). Carries the failing
    sub-profile + a DOCS_URL so the caller can recover programmatically.

    Spec: docs/superpowers/specs/2026-05-16-controller-budget-vs-
    blocking-discovery-design.md §Design / Confidence gate.

    The exception deliberately does NOT carry a "suggested config"
    because the only material the controller has to suggest from is
    config_v0 + the priors that produced the RED commit -- handing those
    back as a suggestion is a footgun (looks authoritative; isn't).
    """

    DOCS_URL = (
        "https://github.com/benseverndev-oss/goldenmatch/blob/main/"
        "docs/explicit-config.md"
    )

    def __init__(
        self,
        *,
        n_rows: int,
        failing_sub_profile: str,
        stop_reason: str,
    ) -> None:
        self.n_rows = n_rows
        self.failing_sub_profile = failing_sub_profile
        self.stop_reason = stop_reason
        super().__init__(
            f"AutoConfigController committed a RED config on a "
            f"{n_rows}-row input (failing sub-profile: {failing_sub_profile}, "
            f"stop_reason: {stop_reason}). Running this config would produce "
            f"degenerate dedupe; passing it back instead of running. "
            f"Options: pass an explicit GoldenMatchConfig, lower the matchkey "
            f"threshold, or re-call with confidence_required=False. See "
            f"{self.DOCS_URL}."
        )


# Priority order for failing-sub-profile diagnostics: root causes
# upstream first. Spec §Design / Confidence gate.
_SUBPROFILE_PRIORITY_ORDER = ("data", "blocking", "scoring", "matchkey", "cluster")


def _identify_failing_subprofile(profile: ComplexityProfile) -> str:  # pyright: ignore[reportUnusedFunction]
    """Walk the ComplexityProfile sub-profiles in priority order; return
    the name of the first one reporting RED. Returns '' when none are
    RED (defensive -- the confidence gate's RED precondition guarantees
    at least one will be).

    Priority order [data, blocking, scoring, matchkey, cluster] surfaces
    upstream causes first: if data is RED, blocking RED is a consequence;
    if blocking is RED, scoring RED is a consequence; etc.
    """
    n_rows = profile.data.n_rows
    health_calls = {
        "data": lambda: profile.data.health(),
        "blocking": lambda: profile.blocking.health(n_rows=n_rows),
        "scoring": lambda: profile.scoring.health(),
        "matchkey": lambda: profile.matchkey.health(),
        "cluster": lambda: profile.cluster.health(n_rows=n_rows),
    }
    for name in _SUBPROFILE_PRIORITY_ORDER:
        if health_calls[name]() == HealthVerdict.RED:
            return name
    return ""


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

    @classmethod
    def for_dataset(cls, n_rows: int) -> ControllerBudget:
        """Calibrate budget + sample size to the input row count.

        Spec §Design / ControllerBudget.for_dataset. Sqrt-scaling above
        100K preserves expected dup-pair density in the sample within an
        order of magnitude as N grows from 100K -> 1M. Cap at 20K so
        sample-iteration cost stays bounded above 1M.

        Tiers (n_rows -> max_seconds, sample_size_default):
          - <5K        -> 15s, 2K (sample_skip_below bypasses sampling)
          - 5K-100K    -> 30s, 2K (historical defaults; preserves 100K bench)
          - 100K-1M    -> 60s, int(sqrt(n) * 20) capped at 20K
          - >=1M       -> 120s, 20K (capped)
        """
        if n_rows < 5_000:
            return cls(max_seconds=15.0)
        if n_rows < 100_000:
            return cls()  # historical defaults
        if n_rows < 1_000_000:
            sample = min(int((n_rows**0.5) * 20), 20_000)
            return cls(sample_size_default=sample, max_seconds=60.0)
        return cls(sample_size_default=20_000, max_seconds=120.0)


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
        df: Any,  # pl.DataFrame | ray.data.Dataset (Phase 2)
        *,
        reference: pl.DataFrame | None = None,
        v0_kwargs: dict | None = None,
        skip_finalize: bool = False,
        confidence_required: bool = True,
    ) -> tuple[GoldenMatchConfig, ComplexityProfile, RunHistory]:
        """Run iterative auto-config.

        Returns (committed_config, full_data_profile, history). Pathological
        inputs short-circuit before the loop:
          - empty df → ConfigValidationError("no data to configure on")
          - all-null cols → ConfigValidationError("no usable columns")
          - n_rows == 1 → v0 + YELLOW + history empty
          - 1 user column → v0 + YELLOW + history empty

        Phase 2: ``df`` may also be a ``ray.data.Dataset``. When it is, the
        controller materializes a small (5K-row) Polars sample for the
        pathological gates and ``_initial_config``, uses
        ``take_sample_distributed`` for the iteration sample, and routes the
        full-df indicator calls through the dispatch shims in
        ``core/indicators.py`` (which themselves collect a bounded sample on
        the distributed path). The iteration loop itself is unchanged — it
        always consumes Polars samples.
        """
        # --- Phase 2: distributed branch detection --------------------------
        from goldenmatch.distributed._utils import is_ray_dataset
        distributed = is_ray_dataset(df)

        if distributed:
            from goldenmatch.distributed.sample import take_sample_distributed as _tsd
            # Materialize a 5K-row sample for gates + _initial_config.
            # Never collects the full dataset on the driver.
            init_sample: pl.DataFrame = _tsd(df, sample_cap=5000)
            n_rows: int = df.count()  # type: ignore[union-attr]
            _df_for_gates = init_sample
        else:
            _df_for_gates = df  # type: ignore[assignment]
            n_rows = df.height  # type: ignore[union-attr]
            init_sample = df  # type: ignore[assignment]  # alias; never collected twice

        # Pathological gates ------------------------------------------------
        if _df_for_gates.height == 0:
            raise ConfigValidationError("no data to configure on")

        user_cols = [c for c in _df_for_gates.columns if not c.startswith("__")]
        if not user_cols:
            raise ConfigValidationError("no usable columns")

        # Check all-null defensively across user columns
        all_null = True
        for col in user_cols:
            if _df_for_gates[col].drop_nulls().len() > 0:
                all_null = False
                break
        if all_null:
            raise ConfigValidationError("no usable columns (all values null)")

        # Single non-empty column or single row → return v0 yellow, skip loop
        if n_rows == 1 or len(user_cols) == 1:
            v0 = self._initial_config(
                init_sample,
                reference=reference,
                v0_kwargs=v0_kwargs,
                n_rows_full=n_rows,
            )
            yellow_profile = self._yellow_sentinel_profile(n_rows, user_cols)
            return v0, yellow_profile, RunHistory()

        # Diag flush prints to localize 5M Linux hang. controller.run was found
        # to be the hidden hang site after PRs #310-#314 attacked the wrong
        # layers (bench heartbeat stage dict missed full-df-only substeps here).
        _diag_t0 = time.time()
        def _diag(msg: str) -> None:
            print(f"[controller.run n_rows={n_rows}] t={time.time()-_diag_t0:.1f}s: {msg}", flush=True)
        _diag("entry")

        # Iteration loop (Task 4.2)
        # Phase 2: distributed path uses init_sample for _initial_config and
        # take_sample_distributed for the iteration sample. Polars path unchanged.
        if distributed:
            from goldenmatch.distributed.sample import take_sample_distributed as _tsd2
            config_v0 = self._initial_config(
                init_sample,
                reference=reference,
                v0_kwargs=v0_kwargs,
                n_rows_full=n_rows,
            )
            _diag("_initial_config done (distributed)")
            sample: pl.DataFrame = _tsd2(df, sample_cap=20_000)
            sample_ref: pl.DataFrame | None = None  # stratified by reference: Phase 3
            _diag(f"take_sample_distributed done (sample.height={sample.height})")
        else:
            config_v0 = self._initial_config(
                df,
                reference=reference,
                v0_kwargs=v0_kwargs,
                n_rows_full=n_rows,
            )  # type: ignore[arg-type]
            _diag("_initial_config done")
            sample, sample_ref = self._take_sample(df, reference=reference)  # type: ignore[arg-type]
            _diag(f"_take_sample done (sample.height={sample.height})")
        history = RunHistory()
        config_n = config_v0
        start = time.time()

        # Task 6.1: eager indicator compute — runs once, before the iteration loop,
        # on the full df (not the sample) so blocking cardinality/overlap signals
        # are representative.
        # Phase 2: route through dispatch shims so the distributed path collects
        # a bounded sample internally rather than materializing the full dataset.
        from goldenmatch.core.indicators import (
            dispatch_compute_column_priors,
            dispatch_estimate_sparse_match_signal,
        )
        column_priors = dispatch_compute_column_priors(df)
        _diag("compute_column_priors done")

        # v1.11: eager NE promotion — runs before the iteration loop so that
        # identity-prior columns (phone, address, etc.) are added as negative
        # evidence on weighted matchkeys before the first iteration profiles them.
        # For the distributed path, use the init_sample (already a Polars frame).
        from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence
        _ne_df = init_sample if distributed else df  # type: ignore[assignment]
        config_v0 = promote_negative_evidence(config_v0, _ne_df, column_priors)
        _diag("promote_negative_evidence done")
        config_n = config_v0

        exact_columns: list[str] = []
        for mk in config_v0.get_matchkeys():
            if mk.type == "exact":
                for f in mk.fields:
                    if f.field is not None:
                        exact_columns.append(f.field)
        sparsity_verdict = dispatch_estimate_sparse_match_signal(df, exact_columns=exact_columns)
        _diag(f"estimate_sparse_match_signal done (exact_cols={len(exact_columns)})")
        # IndicatorContext requires a Polars DataFrame for lazy indicator calls.
        # On the distributed path we supply the init_sample (already materialized).
        _ctx_df = init_sample if distributed else df  # type: ignore[assignment]
        ctx = IndicatorContext(
            df=_ctx_df,
            column_priors=column_priors,
            sparsity_verdict=sparsity_verdict,
        )

        # Phase 3: Component 1 -- one PreparedRecordStore shared across all
        # iterations within this controller.run() call. Phase 2's pipeline-side
        # branch reads/writes via the kwarg threaded through _run_pipeline_sample
        # and _finalize. All 5 iterations share the same DuckDB handle so iter
        # 2-5 hit the disk store instead of re-running prep steps.
        _prep_store: PreparedRecordStore | None = None
        if config_v0.prepared_record_store:
            from goldenmatch.distributed.record_store import PreparedRecordStore as _PRS
            base_dir = os.environ.get("GOLDENMATCH_PREPARED_RECORD_STORE_DIR")
            persist = os.environ.get(
                "GOLDENMATCH_PREPARED_RECORD_STORE_PERSIST", "0"
            ).lower() in ("1", "true", "yes")
            store_path = (
                Path(base_dir) / "goldenmatch_prepared.duckdb"
                if base_dir is not None
                else None
            )
            if store_path is not None:
                _prep_store = _PRS(path=store_path, cleanup=not persist)
            else:
                _prep_store = _PRS(cleanup=not persist)

        try:
            _diag("entering iteration loop")
            for iteration in range(self.budget.max_iterations + 1):
                elapsed = time.time() - start
                if elapsed > self.budget.max_seconds and iteration > 0:
                    history.stop_reason = StopReason.BUDGET_TIME
                    break
                iter_start = time.time()
                _diag(f"iter {iteration} start")
                try:
                    from goldenmatch.core.profile_emitter import profile_capture
                    with profile_capture() as emitter:
                        if _prep_store is not None:
                            self._run_pipeline_sample(
                                sample, sample_ref, config_n,
                                _prep_store=_prep_store,
                            )
                        else:
                            self._run_pipeline_sample(sample, sample_ref, config_n)
                    _diag(f"iter {iteration} _run_pipeline_sample done in {time.time()-iter_start:.1f}s")
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

                # #125: ExpandSample intercept. If the rule that fired
                # this iteration set ``expand_sample``, resample df with
                # a larger sample cap before the next iteration. Capped
                # at 5x the initial cap; beyond that the rule's
                # diminishing returns don't justify the wall-clock.
                _last_entry = history.entries[-1] if history.entries else None
                _last_decision = _last_entry.decision if _last_entry else None
                _expand_factor = (
                    getattr(_last_decision, "expand_sample", None)
                    if _last_decision is not None else None
                )
                if _expand_factor is not None and not distributed:
                    _factor = float(_expand_factor)
                    if not hasattr(self, "_initial_sample_cap"):
                        self._initial_sample_cap = sample.height  # type: ignore[has-type]
                    new_cap = int(sample.height * _factor)
                    if new_cap <= 5 * self._initial_sample_cap:
                        # Bump budget + resample. Polars path only;
                        # distributed sample shape is fixed at take time.
                        self.budget.sample_size_default = new_cap
                        sample, sample_ref = self._take_sample(
                            df, reference=reference,  # type: ignore[arg-type]
                        )
                        _diag(
                            f"ExpandSample({_factor}x): resampled to "
                            f"height={sample.height}",
                        )
                    else:
                        _diag(
                            f"ExpandSample({_factor}x): capped at 5x initial "
                            f"({5 * self._initial_sample_cap})",
                        )
        finally:
            history.elapsed = timedelta(seconds=time.time() - start)
            if _prep_store is not None:
                _prep_store.close()

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
            # Confidence gate (followup to Phase 3 of controller-budget spec):
            # the all-iterations-errored path is the same user-facing
            # pathology as a RED committed entry -- caller would run the
            # full pipeline on the _RED_PROFILE sentinel and produce
            # degenerate output. Refuse loudly at scale.
            if confidence_required and n_rows >= REFUSE_AT_N:
                raise ControllerNotConfidentError(
                    n_rows=n_rows,
                    failing_sub_profile="data",  # n_errored=all means data path itself failed
                    stop_reason=(
                        history.stop_reason.name if history.stop_reason else "unset"
                    ),
                )
            return config_v0, _RED_PROFILE, history

        # Confidence gate (Phase 3 of controller-budget pathology spec).
        # When the controller committed a RED entry on a large input,
        # running the full pipeline would produce ~26-min degenerate
        # dedupe. Refuse loudly instead. Spec §Design / Confidence gate.
        if (
            confidence_required
            and n_rows >= REFUSE_AT_N
            and best_entry.profile.health() == HealthVerdict.RED
        ):
            failing = _identify_failing_subprofile(best_entry.profile)
            # ``_LAST_CONTROLLER_RUN`` here is the CONTROLLER-LOCAL ContextVar
            # defined at the top of this file (line ~45), NOT the
            # ``(profile, history)`` tuple ContextVar in ``autoconfig.py``.
            # Mirror the existing pattern (line 456, line 549) that sets it
            # right before each return.
            _LAST_CONTROLLER_RUN.set(history)  # surface history before raise
            raise ControllerNotConfidentError(
                n_rows=n_rows,
                failing_sub_profile=failing,
                stop_reason=(
                    history.stop_reason.name
                    if history.stop_reason
                    else "unset"
                ),
            )

        # #408: blocking-degenerate gate. Independent of the RED-health
        # check above -- a config can score GREEN/YELLOW on the matchkey
        # sub-profile (good identity signal) AND still commit a blocking
        # strategy that produces singleton blocks (e.g. NPI matchkey AND
        # NPI blocking key). The downstream sync would scan every row
        # without producing useful candidate pairs.
        #
        # Gated on n_rows >= REFUSE_AT_N (same threshold as the existing
        # RED-health guard) so small test fixtures + ad-hoc small dedupes
        # don't trip the gate. Sub-100K-row runs are inherently
        # degenerate-shaped (any blocking strategy looks "thin" because
        # the data is small) but they're also cheap to run regardless,
        # so we let them through silently as today.
        #
        # Estimate avg block size on the sample, scale to full population.
        # Trigger only when confidence_required (default True) -- caller
        # opts out via confidence_required=False to keep today's
        # "warn-and-run" behavior on degenerate blocking.
        if (
            confidence_required
            and n_rows >= REFUSE_AT_N
            and best_entry.config.blocking
            and best_entry.config.blocking.keys
        ):
            from goldenmatch.core.blocking_candidates import (
                degenerate_guard_threshold,
                estimate_avg_block_size,
            )
            _block_fields: list[str] = []
            for _key in best_entry.config.blocking.keys:
                if _key.fields:
                    _block_fields.extend(_key.fields)
            if _block_fields:
                _avg_block_size = estimate_avg_block_size(
                    sample, _block_fields, n_rows,
                )
                if _avg_block_size < degenerate_guard_threshold():
                    _LAST_CONTROLLER_RUN.set(history)
                    raise ControllerNotConfidentError(
                        n_rows=n_rows,
                        failing_sub_profile="blocking",
                        stop_reason=StopReason.BLOCKING_DEGENERATE.name,
                    )

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
        from goldenmatch.core.autoconfig_planner_rules import DEFAULT_RULES
        from goldenmatch.core.runtime_profile import capture_runtime_profile

        runtime = capture_runtime_profile()
        # Extrapolate the committed (sample) blocking profile to full-row count
        # so the planner rules in later phases see signals at full scale.
        committed_profile = best_entry.profile
        if committed_profile.meta.is_sample and committed_profile.meta.sample_size > 0:
            blocking_full = committed_profile.blocking.extrapolate_to(
                n_rows_sample=committed_profile.meta.sample_size,
                n_rows_full=n_rows,
            )
            profile_for_planner = dataclasses.replace(committed_profile, blocking=blocking_full)
        else:
            profile_for_planner = committed_profile

        # Rule 7 (user_override) consumes ``context["user_backend"]``.
        # auto_configure_df does not yet expose a ``backend`` kwarg, so the
        # signal is always None here -- Rule 7 stays a no-op in production
        # until the kwarg lands. Threading the context now means the
        # infrastructure is in place and rule_user_override is unit-testable
        # in isolation.
        plan = apply_planner_rules(
            profile=profile_for_planner,
            runtime=runtime,
            n_rows_full=n_rows,
            rules=DEFAULT_RULES,
            context={"user_backend": None},
        )
        plan.apply_to(committed_config)
        history.execution_plan = plan

        # Fix 4: When skip_finalize=True (called from _api zero-config path),
        # OR when running on a distributed dataset (Phase 2: _finalize takes a
        # Polars DataFrame; distributing finalize is Phase 3),
        # skip the full-data _finalize run. Return the best sample profile.
        if skip_finalize or distributed:
            # Return the best sample profile in lieu of a full-data profile.
            # history.full_vs_sample_drift is left None (drift not computed).
            # For the distributed path, pass init_sample to _record_run so
            # profile_signature can build a shape hash from column names/dtypes.
            _record_df = init_sample if distributed else df  # type: ignore[assignment]
            self._record_run(_record_df, reference, best_entry, history)
            _LAST_CONTROLLER_RUN.set(history)
            return committed_config, best_entry.profile, history

        # Finalize on full data (Task 4.3)
        profile_full = self._finalize(committed_config, df, reference)  # type: ignore[arg-type]
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

        self._record_run(df, reference, best_entry, history)  # type: ignore[arg-type]
        _LAST_CONTROLLER_RUN.set(history)
        return committed_config, profile_full, history

    # ---- Internals --------------------------------------------------------
    def _initial_config(
        self,
        df: pl.DataFrame,
        *,
        reference: pl.DataFrame | None,
        v0_kwargs: dict | None = None,
        n_rows_full: int | None = None,
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

        kw = dict(v0_kwargs or {})
        # #410: thread the full-population row count into v0 so the
        # blocking-candidate gate (build_blocking) can Chao1-project
        # the sample's cardinality to full scale. Without this, v0
        # reads df.height (the SAMPLE's height) as total_rows and the
        # gate's Chao1 short-circuit fires (sample_n == full_n).
        if n_rows_full is not None and "n_rows_full" not in kw:
            kw["n_rows_full"] = n_rows_full

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

        # #131: stratified sampling. When a mid-cardinality column is
        # available (typically zip, state, or similar blocking-shaped
        # column), stratify by it so rare values get representation.
        # Random sampling under-represents the long-tail on heavy-tailed
        # distributions, skewing the controller's cardinality estimates.
        if os.environ.get("GOLDENMATCH_AUTOCONFIG_SAMPLE_STRATEGY", "").lower() != "random":
            strat_key = _pick_stratification_key(df)
            if strat_key is not None:
                return _stratified_sample(
                    df, strat_key, n,
                    min_per_stratum=10, seed=seed,
                )

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
        *,
        _prep_store: PreparedRecordStore | None = None,
    ) -> None:
        """Run the lightweight pipeline (blocking → score → cluster) on the sample.

        Uses ``run_dedupe_df`` / ``run_match_df`` directly so the same instrumented
        stages run; the active ``profile_capture()`` collects sub-profiles.

        ``_prep_store``: when supplied (Phase 3 controller path), threaded through
        to ``run_dedupe_df`` so all iterations within one controller.run() call share
        the same DuckDB-backed prepared-record store. The caller (controller.run)
        owns the store lifecycle.
        """
        from goldenmatch.core.pipeline import run_dedupe_df, run_match_df
        if reference is None:
            run_dedupe_df(sample, config=config, _prep_store=_prep_store)
        else:
            run_match_df(sample, reference, config=config)

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
