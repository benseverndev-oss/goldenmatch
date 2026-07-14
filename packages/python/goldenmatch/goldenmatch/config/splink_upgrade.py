"""Data-aware upgrade pass over a converted Splink config.

Spec: docs/superpowers/specs/2026-07-14-splink-migration-upgrade-design.md

A faithful Splink -> GoldenMatch conversion (``from_splink``) is the trust
anchor: pure, deterministic, data-free. This module runs AFTER conversion,
with the user's data in hand, and applies three independent, individually
skippable levers (TF tables, measured distance thresholds, threshold
calibration) to produce an upgraded config -- plus (optionally) a measured
baseline-vs-upgraded comparison. The converter itself is never modified or
imported for its side effects; this module only reads its public result
shape (``SplinkConversion``).

Task U1 (this file, first cut) builds the scaffold: dataclasses, sampling,
upfront column validation, and the lever-dispatch/skip semantics. The three
lever BODIES are stubs (``NotImplementedError``) until Tasks U2-U4 land;
measurement is wired in Task U5.
"""
from __future__ import annotations

import copy
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from goldenmatch.config.from_splink import ConversionReport, SplinkConversion
from goldenmatch.config.schemas import GoldenMatchConfig
from goldenmatch.core._paths import safe_path
from goldenmatch.core.probabilistic import EMResult


class SplinkUpgradeError(ValueError):
    """Raised on unusable input to :func:`upgrade_splink_conversion` (bad
    data source, missing matchkey columns, unknown lever name)."""


# ── Result dataclasses ───────────────────────────────────────────────────────


@dataclass
class RunStats:
    """Cluster-shape + wall-time summary for one measurement run."""

    cluster_count: int
    multi_record_clusters: int
    max_cluster_size: int
    singleton_count: int
    wall_seconds: float
    snowball: bool = False  # max_cluster_size > 10x reference max


@dataclass
class PairwiseAgreement:
    """Pairwise P/R/F1 for both runs against an external cluster reference."""

    baseline: dict[str, float]
    upgraded: dict[str, float]


@dataclass
class TruthMetrics:
    """Pairwise + B-cubed P/R/F1 for both runs against ground-truth labels."""

    baseline: dict[str, float]
    upgraded: dict[str, float]


@dataclass
class MeasurementResult:
    sample_rows: int
    sampled: bool
    baseline: RunStats
    upgraded: RunStats
    vs_splink: PairwiseAgreement | None = None
    vs_labels: TruthMetrics | None = None


@dataclass
class MigrationResult:
    baseline_config: GoldenMatchConfig       # conversion.config, untouched
    upgraded_config: GoldenMatchConfig       # deep copy with lever changes applied
    em_model: EMResult | None                # upgraded copy (TF tables etc.); baseline untouched
    report: ConversionReport                 # conversion findings + "upgrade:"-prefixed findings
    measurement: MeasurementResult | None = None


# ── Data loading / sampling ──────────────────────────────────────────────────


def _load_frame(data: pl.DataFrame | str | Path) -> pl.DataFrame:
    """Load ``data`` into a polars DataFrame.

    DataFrames pass through unchanged. ``str``/``Path`` are read by suffix
    (``.parquet`` -> ``read_parquet``, ``.csv`` -> ``read_csv``); anything
    else raises :class:`SplinkUpgradeError`.
    """
    if isinstance(data, pl.DataFrame):
        return data

    if isinstance(data, (str, Path)):
        path = safe_path(data)
        suffix = path.suffix.lower()
        if suffix == ".parquet":
            return pl.read_parquet(path)
        if suffix == ".csv":
            return pl.read_csv(path)
        raise SplinkUpgradeError(
            f"upgrade_splink_conversion(): unsupported data file suffix "
            f"'{suffix}' for {path} (expected .parquet or .csv)"
        )

    raise SplinkUpgradeError(
        "upgrade_splink_conversion(): data must be a polars DataFrame, str, "
        f"or Path, got {type(data).__name__}"
    )


def _sample(df: pl.DataFrame, cap: int, seed: int) -> tuple[pl.DataFrame, bool]:
    """Return a seeded subsample of ``df`` capped at ``cap`` rows.

    Returns ``(df, False)`` unchanged when ``len(df) <= cap``, else
    ``(df.sample(n=cap, seed=seed), True)`` -- deterministic across calls
    with the same seed.
    """
    if len(df) > cap:
        return df.sample(n=cap, seed=seed), True
    return df, False


def _validate_columns(config: GoldenMatchConfig, df: pl.DataFrame) -> None:
    """Raise :class:`SplinkUpgradeError` naming any matchkey field missing
    from ``df``'s columns. Runs BEFORE any lever/finding is emitted.

    ``__record__`` is the synthesized field name ``record_embedding`` scorer
    fields resolve to (schemas.py's ``MatchkeyField._resolve_field_column``)
    and carries no single data column, so it's skipped.
    """
    mks = config.get_matchkeys()
    if not mks:
        return
    mk = mks[0]
    columns = set(df.columns)
    missing = [
        f.field
        for f in mk.fields
        if f.field is not None and f.field != "__record__" and f.field not in columns
    ]
    if missing:
        raise SplinkUpgradeError(
            "upgrade_splink_conversion(): matchkey field(s) missing from data "
            f"columns: {missing}"
        )


# ── Lever context + registry ─────────────────────────────────────────────────


@dataclass
class _LeverContext:
    """Mutable working state threaded through the lever registry.

    ``upgraded_config`` / ``em_model`` are the copy-on-write targets levers
    mutate in place; ``report`` accumulates findings (all under an
    ``upgrade:``-prefixed splink_path); ``df`` is the (already sampled) data.
    """

    conversion: SplinkConversion
    upgraded_config: GoldenMatchConfig
    em_model: EMResult | None
    report: ConversionReport
    df: pl.DataFrame
    seed: int


_BARE_SETTINGS_SKIP_MSG = (
    "skipped: no imported model; run-time EM training computes TF tables "
    "and calibrated thresholds natively"
)


def _lever_tf_tables(ctx: _LeverContext) -> None:
    if ctx.conversion.em_model is None:
        ctx.report.info("upgrade:tf_tables", _BARE_SETTINGS_SKIP_MSG, mapped_to="em.tf_freqs")
        return
    # U2 replaces this stub with the real _build_tf_tables-backed lever.
    raise NotImplementedError("tf_tables lever body lands in Task U2")


def _lever_distance_thresholds(ctx: _LeverContext) -> None:
    # Applies regardless of em_model presence (band thresholds are
    # config-level, fixed before training) -- see spec "Bare-settings inputs".
    # U3 replaces this stub with the real lever.
    raise NotImplementedError("distance_thresholds lever body lands in Task U3")


def _lever_calibration(ctx: _LeverContext) -> None:
    if ctx.conversion.em_model is None:
        ctx.report.info("upgrade:calibration", _BARE_SETTINGS_SKIP_MSG, mapped_to=None)
        return
    # U4 replaces this stub with the real lever.
    raise NotImplementedError("calibration lever body lands in Task U4")


_LEVER_REGISTRY: dict[str, Callable[[_LeverContext], None]] = {
    "tf_tables": _lever_tf_tables,
    "distance_thresholds": _lever_distance_thresholds,
    "calibration": _lever_calibration,
}

_LEVER_ORDER: tuple[str, ...] = ("tf_tables", "distance_thresholds", "calibration")


def _resolve_levers(levers: Iterable[str] | None) -> list[str]:
    """Resolve the requested lever subset (in canonical order); raise on any
    unknown name."""
    if levers is None:
        return list(_LEVER_ORDER)
    requested = set(levers)
    unknown = requested - set(_LEVER_REGISTRY)
    if unknown:
        raise SplinkUpgradeError(
            f"upgrade_splink_conversion(): unknown lever(s) {sorted(unknown)}; "
            f"valid levers are {sorted(_LEVER_REGISTRY)}"
        )
    return [name for name in _LEVER_ORDER if name in requested]


# ── Orchestrator ──────────────────────────────────────────────────────────────


def upgrade_splink_conversion(
    conversion: SplinkConversion,
    data: pl.DataFrame | str | Path,
    *,
    sample_cap: int = 100_000,
    seed: int = 42,
    splink_clusters: pl.DataFrame | str | Path | None = None,
    labels: pl.DataFrame | str | Path | None = None,
    levers: set[str] | None = None,
    measure: bool = True,
) -> MigrationResult:
    """Run the data-aware upgrade pass over a converted Splink config.

    Args:
        conversion: Result of :func:`goldenmatch.config.from_splink.from_splink`.
            Never mutated -- ``conversion.config``/``conversion.em_model``/
            ``conversion.report`` are all copied before any lever runs.
        data: The dataset to measure levers against: a polars DataFrame, or
            a path (``.parquet``/``.csv``) to load one from.
        sample_cap: Above this row count, ``data`` is seeded-subsampled
            before any lever/measurement runs (bounds cost at scale).
        seed: Seed for the subsample (deterministic across calls).
        splink_clusters: Optional id -> cluster_id reference (migrators'
            prior Splink output) for pairwise-agreement measurement.
        labels: Optional ground-truth id -> cluster_id for true P/R/F1 +
            B-cubed measurement.
        levers: Subset of ``{"tf_tables", "distance_thresholds",
            "calibration"}`` to run; ``None`` (default) runs all three in
            that order. Unknown names raise :class:`SplinkUpgradeError`.
        measure: When True (default), runs baseline-vs-upgraded measurement
            on the sample. ``False`` skips measurement (an info finding
            records why); also the escape hatch for scale where running
            both configs is infeasible.

    Returns:
        A :class:`MigrationResult` with the untouched baseline config, a
        deep-copied upgraded config with lever changes applied, an upgraded
        ``EMResult`` copy (when the input was trained), the extended
        conversion report, and (when ``measure=True``) a measurement.

    Raises:
        SplinkUpgradeError: on unusable ``data``, a matchkey field missing
            from ``data``'s columns, or an unknown lever name.
    """
    df = _load_frame(data)

    # Upfront validation happens BEFORE any finding is emitted (spec
    # invariant) -- even the sampling info note comes after this.
    _validate_columns(conversion.config, df)

    lever_names = _resolve_levers(levers)

    # Copy-on-write: never mutate the input conversion. NOTE: EMResult's
    # to_dict()/from_dict() round-trip is a plain projection -- the nested
    # m/u/weight dicts and lists pass through BY REFERENCE -- so it is NOT a
    # safe copy mechanism (levers mutating tf_freqs/match_weights in place
    # would corrupt the baseline model). deepcopy is the dataclass-safe copy.
    upgraded_config = GoldenMatchConfig(**conversion.config.model_dump())
    em_model = copy.deepcopy(conversion.em_model)
    # Findings are flat dataclasses (str/None fields only), so a per-finding
    # shallow copy fully isolates them from later mutation.
    report = ConversionReport(findings=[copy.copy(f) for f in conversion.report.findings])

    sampled_df, sampled = _sample(df, sample_cap, seed)
    if sampled:
        report.info(
            "upgrade:sample",
            f"data sampled to {sample_cap} rows (seed={seed}) for lever "
            "measurement and computation; full dataset had "
            f"{len(df)} rows",
            mapped_to=None,
        )

    ctx = _LeverContext(
        conversion=conversion,
        upgraded_config=upgraded_config,
        em_model=em_model,
        report=report,
        df=sampled_df,
        seed=seed,
    )

    for name in lever_names:
        _LEVER_REGISTRY[name](ctx)

    measurement: MeasurementResult | None = None
    if measure:
        # U5: wires goldenmatch.config.splink_upgrade_measure.run_measurement
        # here. Until then, record why measurement isn't present yet.
        report.info(
            "upgrade:measure",
            "measurement not yet wired",
            mapped_to=None,
        )
        measurement = None

    return MigrationResult(
        baseline_config=conversion.config,
        upgraded_config=ctx.upgraded_config,
        em_model=ctx.em_model,
        report=report,
        measurement=measurement,
    )
