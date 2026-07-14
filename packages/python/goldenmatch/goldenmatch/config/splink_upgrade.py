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
import math
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

    from goldenmatch.core.probabilistic import _build_tf_tables

    assert ctx.em_model is not None  # guaranteed by the branch above (copy of conversion.em_model)

    mk = ctx.upgraded_config.get_matchkeys()[0]
    existing_tf_freqs = ctx.em_model.tf_freqs or {}
    needed_fields = [
        f for f in mk.fields
        if getattr(f, "tf_adjustment", False) and f.field is not None
        and f.field not in existing_tf_freqs
    ]
    already_present = [
        f.field for f in mk.fields
        if getattr(f, "tf_adjustment", False) and f.field is not None
        and f.field in existing_tf_freqs
    ]
    for field_name in already_present:
        ctx.report.info(
            "upgrade:tf_tables",
            f"field '{field_name}' already has a TF table, skipped",
            mapped_to="em.tf_freqs",
        )

    if not needed_fields:
        if not already_present:
            ctx.report.info(
                "upgrade:tf_tables", "no fields need TF tables", mapped_to="em.tf_freqs"
            )
        return

    # _build_tf_tables computes tables for ALL tf_adjustment fields on mk in
    # one call; we only merge the ones that are actually needed below. Fields
    # with an entirely-null/empty data column come back empty from
    # value_frequencies and are warned+skipped individually.
    tf_freqs, tf_collision = _build_tf_tables(ctx.df, mk)
    tf_freqs = tf_freqs or {}
    tf_collision = tf_collision or {}

    new_tf_freqs = dict(existing_tf_freqs)
    new_tf_collision = dict(ctx.em_model.tf_collision or {})
    for f in needed_fields:
        field_name = f.field
        assert field_name is not None
        freqs = tf_freqs.get(field_name)
        if not freqs:
            # Either the column is entirely null/empty, or the field's column
            # isn't present in ctx.df at all (already guarded by upfront
            # validation for matchkey fields, so this is the null-column case).
            ctx.report.warn(
                "upgrade:tf_tables",
                f"field '{field_name}' has no non-null values to build a TF "
                "table from, skipped",
                mapped_to="em.tf_freqs",
            )
            continue
        new_tf_freqs[field_name] = freqs
        new_tf_collision[field_name] = tf_collision.get(
            field_name, sum(p * p for p in freqs.values())
        )
        ctx.report.info(
            "upgrade:tf_tables",
            f"field '{field_name}': TF table built ({len(freqs)} distinct "
            f"values, collision rate {new_tf_collision[field_name]:.4f})",
            mapped_to="em.tf_freqs",
        )

    if new_tf_freqs:
        ctx.em_model.tf_freqs = new_tf_freqs
        ctx.em_model.tf_collision = new_tf_collision


def _measure_mean_length(df: pl.DataFrame, field: str, transforms: list[str]) -> float | None:
    """Mean post-transform length of ``field``'s non-null, non-empty sample
    values.

    Mirrors the per-value transform loop in
    ``goldenmatch.core.tf_tables.value_frequencies`` (the same route the
    tf_tables lever, U2, reuses via ``_build_tf_tables``) -- same
    ``apply_transforms`` semantics, same null/empty-string filtering -- just
    collecting lengths instead of frequencies. Returns ``None`` when the
    column is absent or every value is null/empty after transforms.
    """
    from goldenmatch.utils.transforms import apply_transforms

    if field not in df.columns:
        return None
    total_len = 0
    count = 0
    for v in df[field].to_list():
        if v is None:
            continue
        s = str(v)
        if transforms:
            s = apply_transforms(s, transforms)
        if not s:
            continue
        total_len += len(s)
        count += 1
    if count == 0:
        return None
    return total_len / count


def _lever_distance_thresholds(ctx: _LeverContext) -> None:
    # Applies regardless of em_model presence (band thresholds are
    # config-level, fixed before training) -- see spec "Bare-settings inputs".
    #
    # Mechanism (pinned, no finding-message parsing): every scorer="levenshtein"
    # field in a converted config can only have come from the converter's
    # _DIST_RE path with the constant _LEV_ASSUMED_LEN=10, so the original
    # Splink edit distance inverts exactly: d = round((1 - t) * 10) per
    # threshold. We measure the mean post-transform string length L of the
    # field's data column and recompute sim = max(0, 1 - d/L).
    from goldenmatch.config.from_splink import _LEV_ASSUMED_LEN

    mk = ctx.upgraded_config.get_matchkeys()[0]
    lev_fields = [f for f in mk.fields if f.scorer == "levenshtein" and f.field is not None]

    if not lev_fields:
        ctx.report.info(
            "upgrade:distance_thresholds", "no levenshtein-scorer fields in config",
            mapped_to=None,
        )
        return

    for f in lev_fields:
        field_name = f.field
        assert field_name is not None
        mapped_to = f"matchkeys[0].fields[{field_name}]"

        L = _measure_mean_length(ctx.df, field_name, f.transforms)
        if L is None or L <= 0:
            ctx.report.warn(
                "upgrade:distance_thresholds",
                f"field '{field_name}' has no non-null/non-empty sample values "
                "to measure post-transform length from, skipped (thresholds "
                "unchanged)",
                mapped_to=mapped_to,
            )
            continue

        # is_two_level fields (from_splink's `levels_count == 2` path) carry
        # their single threshold on partial_threshold, never level_thresholds.
        is_two_level = f.level_thresholds is None
        old_thresholds = [f.partial_threshold] if is_two_level else list(f.level_thresholds or [])
        n_old = len(old_thresholds)
        # old_thresholds[i] (descending) maps to GM level (levels-1-i) --
        # mirrors from_splink._agree_index_for's positional resolution.
        old_levels = [f.levels - 1 - i for i in range(n_old)]

        # Recompute per band, grouping adjacent bands whose new similarity
        # collides (equal, or both clamped invalid) onto one GoldenMatch
        # level -- mirrors import_em's collapse handling (sum m/u, warn).
        # `new_t` is monotonically non-increasing as we walk the list (old_t
        # descending -> d ascending -> new_t descending), so collisions are
        # always between adjacent entries and this single forward pass
        # dedupes/sorts correctly without a separate sort step.
        groups: list[dict] = []
        pending: list[int] = []  # old levels whose recompute was out of range,
        # awaiting attachment to the next (lower) surviving group.

        for old_level, old_t in zip(old_levels, old_thresholds):
            d = round((1 - old_t) * _LEV_ASSUMED_LEN)
            new_t = max(0.0, 1 - d / L)
            if not (0.0 < new_t <= 1.0):
                ctx.report.warn(
                    "upgrade:distance_thresholds",
                    f"field '{field_name}': band (old threshold {old_t}, d={d}, "
                    f"measured L={L:.2f}) recomputed to {new_t:.4f}, out of "
                    "(0, 1] -- band dropped and its m/u mass merged into the "
                    "adjacent level",
                    mapped_to=mapped_to,
                )
                pending.append(old_level)
                continue
            if groups and math.isclose(groups[-1]["new_t"], new_t, abs_tol=1e-9):
                groups[-1]["members"].append(old_level)
                ctx.report.warn(
                    "upgrade:distance_thresholds",
                    f"field '{field_name}': recomputed threshold for old band "
                    f"(threshold {old_t}, d={d}) collapsed onto an adjacent "
                    f"GoldenMatch level ({new_t:.4f}); m/u probabilities summed "
                    "with the earlier level's",
                    mapped_to=mapped_to,
                )
            else:
                groups.append({"new_t": new_t, "members": [old_level]})
                ctx.report.info(
                    "upgrade:distance_thresholds",
                    f"field '{field_name}': threshold {old_t} -> {new_t:.4f} "
                    f"(d={d}, measured L={L:.2f})",
                    mapped_to=mapped_to,
                )
            if pending:
                groups[-1]["members"].extend(pending)
                pending = []

        # Any bands still pending after the loop are the LOWEST bands and all
        # were out of range -- their mass has nowhere lower to merge into
        # except the disagree level (0).
        merge_into_level0 = pending

        if not groups:
            ctx.report.warn(
                "upgrade:distance_thresholds",
                f"field '{field_name}': recomputed thresholds leave no valid "
                "band, skipped (thresholds unchanged)",
                mapped_to=mapped_to,
            )
            continue

        new_levels = len(groups) + 1
        new_thresholds = [g["new_t"] for g in groups]

        if is_two_level:
            f.partial_threshold = new_thresholds[0]
        else:
            f.level_thresholds = new_thresholds
            f.levels = new_levels

        if ctx.em_model is not None and field_name in ctx.em_model.m_probs:
            old_m = ctx.em_model.m_probs[field_name]
            old_u = ctx.em_model.u_probs[field_name]
            # groups is ordered highest-new_t-first (top level first); array
            # storage is ascending-index (level0 first), so level0 + reversed
            # group order gives the correct final layout.
            merged_m = [old_m[0] + sum(old_m[j] for j in merge_into_level0)]
            merged_u = [old_u[0] + sum(old_u[j] for j in merge_into_level0)]
            for g in groups:
                merged_m.append(sum(old_m[j] for j in g["members"]))
                merged_u.append(sum(old_u[j] for j in g["members"]))
            new_m = [merged_m[0]] + list(reversed(merged_m[1:]))
            new_u = [merged_u[0]] + list(reversed(merged_u[1:]))

            sum_m = sum(new_m)
            sum_u = sum(new_u)
            if sum_m > 0:
                new_m = [v / sum_m for v in new_m]
            if sum_u > 0:
                new_u = [v / sum_u for v in new_u]

            ctx.em_model.m_probs[field_name] = new_m
            ctx.em_model.u_probs[field_name] = new_u
            ctx.em_model.match_weights[field_name] = [
                math.log2(max(m, 1e-10) / max(u, 1e-10)) for m, u in zip(new_m, new_u)
            ]


# Pair-count cap for calibration scoring -- matches train_em's
# n_sample_pairs default (the same distributional budget EM trains on).
_CALIBRATION_MAX_PAIRS = 10_000

# compute_thresholds' data-driven branch requires len(scored_weights)
# STRICTLY > 50; at or below it silently falls through to fixed defaults
# (0.50, 0.35), which must never be presented as "calibrated".
_CALIBRATION_MIN_PAIRS = 50


def _lever_calibration(ctx: _LeverContext) -> None:
    # Runs AFTER levers 1-2 by design (registry order): it calibrates the
    # thresholds against the model users will actually run.
    if ctx.conversion.em_model is None:
        ctx.report.info("upgrade:calibration", _BARE_SETTINGS_SKIP_MSG, mapped_to=None)
        return

    from goldenmatch.core.probabilistic import (
        _fs_calibration_mode,
        _sample_blocked_pairs,
        comparison_vector,
        compute_thresholds,
    )

    mapped_to = "matchkeys[0].link_threshold/review_threshold"

    # Posterior mode check FIRST, before any pair work: in posterior scoring
    # mode (GOLDENMATCH_FS_CALIBRATED=posterior, read via
    # _fs_calibration_mode) compute_thresholds deliberately returns fixed
    # absolute probability cuts (0.99, 0.50) and ignores the distribution,
    # so there is nothing data-driven to calibrate.
    if _fs_calibration_mode() == "posterior":
        ctx.report.info(
            "upgrade:calibration",
            "skipped: posterior calibration mode (GOLDENMATCH_FS_CALIBRATED) "
            "uses fixed absolute cuts (0.99/0.50) by design; distribution "
            "calibration does not apply",
            mapped_to=mapped_to,
        )
        return

    assert ctx.em_model is not None  # copy of conversion.em_model (checked above)
    em = ctx.em_model
    mk = ctx.upgraded_config.get_matchkeys()[0]

    blocking = ctx.upgraded_config.blocking
    if blocking is None:
        ctx.report.warn(
            "upgrade:calibration",
            "skipped: config has no blocking configuration, cannot enumerate "
            "blocked candidate pairs",
            mapped_to=mapped_to,
        )
        return

    # Candidate pairs -- PREFERRED route (Task U4 investigation): reuse the
    # exact pipeline entrypoints rather than re-deriving blocking semantics.
    # core/pipeline.py builds `blocks = build_blocks(combined_lf,
    # config.blocking)` on a __row_id__-carrying LazyFrame (its private
    # `_add_row_ids` = with_row_index("__row_id__") + Int64 cast, mirrored
    # inline here) and hands them to train_em, which samples via
    # `_sample_blocked_pairs(blocks, n_pairs, seed)`. Calling the same two
    # functions keeps the lever's candidate set identical in shape to what
    # EM trains on; the group-rows-yourself fallback was not needed.
    from goldenmatch.core.blocker import build_blocks

    lf = ctx.df.lazy()
    if "__row_id__" not in ctx.df.columns:
        lf = lf.with_row_index("__row_id__")
    lf = lf.with_columns(pl.col("__row_id__").cast(pl.Int64))
    blocks = build_blocks(lf, blocking)
    pairs = _sample_blocked_pairs(blocks, n_pairs=_CALIBRATION_MAX_PAIRS, seed=ctx.seed)

    if len(pairs) <= _CALIBRATION_MIN_PAIRS:
        ctx.report.warn(
            "upgrade:calibration",
            f"skipped: only {len(pairs)} blocked candidate pair(s) on the "
            f"sample; data-driven threshold calibration needs more than "
            f"{_CALIBRATION_MIN_PAIRS} scored pairs (below that "
            "compute_thresholds falls back to fixed defaults, which would "
            "not be calibrated) -- thresholds left unset",
            mapped_to=mapped_to,
        )
        return

    # Score every candidate pair with the UPGRADED model (post levers 1-2):
    # comparison vector + summed Fellegi-Sunter match weights, with NO
    # link-threshold cut (score_probabilistic filters to survivors; the
    # calibration needs the full distribution). Row lookup mirrors train_em.
    from goldenmatch.core.frame import to_frame

    cols = [f.field for f in mk.fields if f.field is not None and f.field != "__record__"]
    row_lookup: dict[int, dict] = {}
    for row in to_frame(lf.collect()).select_dicts(["__row_id__"] + cols):
        row_lookup[row["__row_id__"]] = row

    # (index-into-comparison-vector, model-weight-key) pairs; converted
    # configs always carry a field name, the filter narrows the Optional.
    indexed_fields = [(k, f.field) for k, f in enumerate(mk.fields) if f.field is not None]

    total_weights: list[float] = []
    for a, b in pairs:
        vec = comparison_vector(row_lookup.get(a, {}), row_lookup.get(b, {}), mk)
        total_weights.append(
            sum(em.match_weights[name][vec[k]] for k, name in indexed_fields)
        )

    # Normalize the SAME way runtime scoring does (score_probabilistic):
    # against the MODEL-derived min/max total weight, not the observed
    # min/max -- at run time mk.link_threshold is compared to model-range
    # normalized scores, so the calibrated cuts must live on that scale.
    max_weight = sum(max(em.match_weights[name]) for _, name in indexed_fields)
    min_weight = sum(min(em.match_weights[name]) for _, name in indexed_fields)
    weight_range = max_weight - min_weight
    if weight_range <= 0:
        ctx.report.warn(
            "upgrade:calibration",
            "skipped: model match-weight range is degenerate (max == min), "
            "cannot normalize pair weights -- thresholds left unset",
            mapped_to=mapped_to,
        )
        return
    normalized = [(w - min_weight) / weight_range for w in total_weights]

    link, review = compute_thresholds(em, scored_weights=normalized)
    mk.link_threshold = link
    mk.review_threshold = review

    sorted_norm = sorted(normalized)
    n = len(sorted_norm)
    p50 = sorted_norm[n // 2]
    p95 = sorted_norm[min(n - 1, int(round(0.95 * (n - 1))))]
    ctx.report.info(
        "upgrade:calibration",
        f"link_threshold={link}, review_threshold={review} calibrated from "
        f"{n} blocked candidate pairs (normalized weight p50={p50:.4f}, "
        f"p95={p95:.4f})",
        mapped_to=mapped_to,
    )


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
