"""Stage-specific complexity sub-profiles + rollup, emitted by instrumented
pipeline stages and consumed by AutoConfigController + RefitPolicy.

Spec: docs/superpowers/specs/2026-05-06-autoconfig-introspective-controller-design.md §Types & contracts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

ColumnType = Literal["text", "numeric", "id-like", "date", "geo", "phone", "email", "name", "unknown"]


class HealthVerdict(Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class StopReason(Enum):
    """Why the controller stopped iterating.

    Set on ``RunHistory.stop_reason`` at each break point in
    ``AutoConfigController.run()``. Observable via
    ``result.postflight_report.controller_history.stop_reason``.
    """
    GREEN = "green"                           # iteration produced a healthy profile
    CONVERGED = "converged"                   # profile distance to prev < epsilon
    BUDGET_ITERATIONS = "budget_iterations"   # max_iterations hit
    BUDGET_TIME = "budget_time"               # max_seconds hit
    POLICY_SATISFIED = "policy_satisfied"     # policy returned None on non-green
    POLICY_NO_PROGRESS = "policy_no_progress" # policy returned identical config
    OSCILLATING = "oscillating"               # is_oscillating() fired
    CANCELLED = "cancelled"                   # KeyboardInterrupt
    # #408: committed blocking strategy would produce singleton blocks
    # (avg block size < threshold). Sync would scan all rows without
    # producing useful candidate pairs. Raise unless caller opts out via
    # confidence_required=False.
    BLOCKING_DEGENERATE = "blocking_degenerate"


@dataclass(frozen=True)
class ColumnPrior:
    """v1.10: per-column priors used by indicator-aware rules.

    identity_score: 0.0-1.0; high for canonical identity columns
    (email, ssn, phone, id-like high-cardinality strings).
    corruption_score: 0.0-1.0; high when within-column edit-distance
    variance suggests typo/case noise (Brian/BRIAN/B.).
    """
    identity_score: float
    corruption_score: float


@dataclass(frozen=True)
class SparsityVerdict:
    """v1.10: result of sparse-match estimation.

    is_sparse: True when sample's exact-matchkey hit count is below
    the heuristic floor (default 50) — sample is too small to surface
    visible matches under v0's matchkey config.
    estimated_n_true_pairs: rough estimate from exact-matchkey hits;
    used as a tiebreak indicator for rule_sparse_match_expand.
    """
    is_sparse: bool
    estimated_n_true_pairs: int


@dataclass(frozen=True)
class CollisionSignal:
    """v1.11: result of identity-column collision detection.

    rate: fraction of multi-record groups (size >= 2) where the witness
    columns disagree by max divergence > 0.5. High rate (>0.2) indicates
    the identity column is collision-prone — same value used for distinct
    entities (T3's adversarial pattern).

    witness_used: name of the witness column that drove the highest
    divergences (used by the demote rule's logging). Empty string when
    no signal could be computed (budget timeout, no witnesses).
    """
    rate: float
    witness_used: str


@dataclass(frozen=True)
class IndicatorsProfile:
    """v1.10: dynamic measurements computed lazily by indicators.

    Default-None fields are populated by IndicatorContext on first
    rule access; they remain None when the rule didn't need them
    (cheap path on YELLOW-reaching benchmarks).
    """
    full_pop_matchkey_hit_rate: float | None = None
    cross_blocking_overlap: float | None = None


def _max_severity(*verdicts: HealthVerdict) -> HealthVerdict:
    if HealthVerdict.RED in verdicts:
        return HealthVerdict.RED
    if HealthVerdict.YELLOW in verdicts:
        return HealthVerdict.YELLOW
    return HealthVerdict.GREEN


@dataclass(frozen=True)
class DataProfile:
    _version: int = 1
    n_rows: int = 0
    n_cols: int = 0
    column_types: dict[str, ColumnType] = field(default_factory=dict)
    cardinality_ratio: dict[str, float] = field(default_factory=dict)
    null_rate: dict[str, float] = field(default_factory=dict)
    value_length_p50: dict[str, int] = field(default_factory=dict)
    value_length_p99: dict[str, int] = field(default_factory=dict)
    column_priors: dict[str, ColumnPrior] | None = None

    def health(self) -> HealthVerdict:
        if self.n_rows == 0:
            return HealthVerdict.RED
        if self.n_cols == 1 or len(set(self.column_types.values())) == 1:
            return HealthVerdict.YELLOW
        return HealthVerdict.GREEN


@dataclass(frozen=True)
class DomainProfile:
    _version: int = 1
    detected_domain: str | None = None
    confidence: float = 0.0
    derived_columns: list[str] = field(default_factory=list)
    low_confidence_row_count: int = 0

    def health(self) -> HealthVerdict:
        if self.confidence < 0.3 and self.derived_columns:
            return HealthVerdict.YELLOW
        return HealthVerdict.GREEN


@dataclass(frozen=True)
class FieldStats:
    post_transform_cardinality_ratio: float
    post_transform_null_rate: float
    post_transform_value_length_p50: int


@dataclass(frozen=True)
class MatchkeyProfile:
    _version: int = 1
    per_field: dict[str, FieldStats] = field(default_factory=dict)

    def health(self) -> HealthVerdict:
        verdicts = []
        for fs in self.per_field.values():
            if fs.post_transform_cardinality_ratio == 0.0:
                verdicts.append(HealthVerdict.RED)
            elif fs.post_transform_cardinality_ratio > 0.95:
                verdicts.append(HealthVerdict.YELLOW)
        return _max_severity(*verdicts) if verdicts else HealthVerdict.GREEN


@dataclass(frozen=True)
class BlockingProfile:
    _version: int = 1
    keys_used: list[list[str]] = field(default_factory=list)
    n_blocks: int = 0
    total_comparisons: int = 0
    reduction_ratio: float = 0.0
    block_sizes_p50: int = 0
    block_sizes_p95: int = 0
    block_sizes_p99: int = 0
    block_sizes_max: int = 0
    singleton_block_count: int = 0
    oversized_block_count: int = 0

    @property
    def estimated_pair_count(self) -> int:
        """Spec §Signals: total candidate pairs at this blocking layout.

        Identical to ``total_comparisons``; surfaced under the
        planner-friendly name so callers don't need to know the underlying
        field name.
        """
        return self.total_comparisons

    def extrapolate_to(self, n_rows_sample: int, n_rows_full: int) -> BlockingProfile:
        """Project sample's pair-count signal to a full-data row count.

        Spec §Pipeline integration: pair count scales linearly with the
        row-count ratio. Over-estimation just pushes toward a heavier
        plan, which is safer than under-estimating. Block-size percentiles
        are not scaled -- distribution shape is roughly invariant to N
        when blocking is well-behaved (spec §Open questions #1).
        """
        import dataclasses

        if n_rows_sample <= 0 or n_rows_full <= 0:
            return self
        ratio = n_rows_full / n_rows_sample
        return dataclasses.replace(
            self,
            n_blocks=int(self.n_blocks * ratio),
            total_comparisons=int(self.total_comparisons * ratio),
            singleton_block_count=int(self.singleton_block_count * ratio),
        )

    def health(self, n_rows: int) -> HealthVerdict:
        if self.n_blocks == 0:
            return HealthVerdict.RED
        avg = n_rows / max(self.n_blocks, 1)
        if self.block_sizes_p99 > 10 * avg:
            return HealthVerdict.RED
        if self.reduction_ratio < 0.5:
            return HealthVerdict.RED
        if self.singleton_block_count / self.n_blocks > 0.5:
            return HealthVerdict.YELLOW
        return HealthVerdict.GREEN


@dataclass(frozen=True)
class ScoringProfile:
    _version: int = 1
    n_pairs_scored: int = 0
    candidates_compared: int = 0
    score_histogram: list[int] = field(default_factory=lambda: [0] * 20)
    dip_statistic: float = 0.0
    mass_above_threshold: float = 0.0
    mass_in_borderline: float = 0.0
    per_field_score_variance: dict[str, float] = field(default_factory=dict)
    # Tier 1a: fraction of random non-blocked pairs whose weighted score >= threshold.
    # None = probe not run (older paths that don't invoke the probe stay valid).
    random_pair_above_threshold_rate: float | None = None

    def health(self) -> HealthVerdict:
        # No candidates compared and no pairs scored → RED (nothing happened)
        if self.candidates_compared == 0 and self.n_pairs_scored == 0:
            return HealthVerdict.RED
        # Candidates were compared but nothing reached threshold → still RED
        # (rule_no_matches handles the "wrong threshold" case)
        if self.mass_above_threshold == 0.0 and self.candidates_compared > 0:
            return HealthVerdict.RED
        if self.mass_above_threshold == 0.0:
            return HealthVerdict.RED
        if self.dip_statistic < 0.005 and self.n_pairs_scored > 0:
            return HealthVerdict.RED
        if self.mass_in_borderline > 0.3:
            return HealthVerdict.YELLOW
        return HealthVerdict.GREEN


@dataclass(frozen=True)
class ClusterProfile:
    _version: int = 1
    n_clusters: int = 0
    cluster_size_p50: int = 0
    cluster_size_p99: int = 0
    cluster_size_max: int = 0
    transitivity_rate: float = 1.0
    edge_confidence_p50: float = 0.0
    edge_confidence_min: float = 0.0
    oversized_cluster_count: int = 0

    def health(self, n_rows: int) -> HealthVerdict:
        if n_rows > 0 and self.cluster_size_max > 0.1 * n_rows:
            return HealthVerdict.RED
        if self.transitivity_rate < 0.85:
            return HealthVerdict.RED
        if self.oversized_cluster_count > 0:
            return HealthVerdict.YELLOW
        return HealthVerdict.GREEN


@dataclass(frozen=True)
class ProfileMeta:
    _version: int = 1
    iteration: int = 0
    is_sample: bool = True
    sample_size: int = 0
    n_rows_full: int = 0
    wall_clock_ms: int = 0
    seed: int = 0


@dataclass(frozen=True)
class ComplexityProfile:
    _version: int = 1
    data: DataProfile = field(default_factory=DataProfile)
    domain: DomainProfile = field(default_factory=DomainProfile)
    matchkey: MatchkeyProfile = field(default_factory=MatchkeyProfile)
    blocking: BlockingProfile = field(default_factory=BlockingProfile)
    scoring: ScoringProfile = field(default_factory=ScoringProfile)
    cluster: ClusterProfile = field(default_factory=ClusterProfile)
    meta: ProfileMeta = field(default_factory=ProfileMeta)
    indicators: IndicatorsProfile | None = None

    def health(self) -> HealthVerdict:
        return _max_severity(
            self.data.health(),
            self.domain.health(),
            self.matchkey.health(),
            self.blocking.health(n_rows=self.data.n_rows),
            self.scoring.health(),
            self.cluster.health(n_rows=self.data.n_rows),
        )

    def normalized_signal_vector(self) -> list[float]:
        """L1-distance vector for convergence detection. v1 picks 8 signals."""
        return [
            min(self.blocking.reduction_ratio, 1.0),
            min(self.blocking.block_sizes_p99 / max(self.data.n_rows, 1), 1.0),
            min(self.scoring.dip_statistic / 0.1, 1.0),
            self.scoring.mass_above_threshold,
            self.scoring.mass_in_borderline,
            self.cluster.transitivity_rate,
            min(self.cluster.cluster_size_max / max(self.data.n_rows, 1), 1.0),
            float(self.cluster.n_clusters > 0),
        ]

    def to_legacy_dict(self) -> dict:
        """Return the typed sub-profiles as the legacy ``PostflightSignals`` dict shape.

        This is the canonical back-compat shim used by ``_signals_view`` in
        ``autoconfig_verify``. Keys match ``PostflightSignals`` exactly so
        consumers that read by key work without change.

        Notes on shape differences vs the legacy postflight() path:
        - ``score_histogram``: returns the typed ``list[int]`` (20 buckets) rather
          than the ``{"bins": [...], "counts": [...]}`` dict produced by the legacy
          postflight path. Consumers that previously expected the dict shape must
          accept the list — they should read from ``controller_profile`` paths only
          when the controller ran, so this is a controlled cut-over.
        - ``blocking_recall``: mapped from ``reduction_ratio`` (same concept; the
          legacy key was mislabelled).
        - ``block_size_percentiles``: reconstructed from the four BlockingProfile
          percentile fields. ``p95`` is not stored in BlockingProfile; 0 is used
          as a sentinel.
        - ``preliminary_cluster_sizes``: reconstructed from ClusterProfile. ``p95``
          and ``count`` are not in ClusterProfile; 0 is used as sentinels.
        - ``current_threshold``: not stored in ComplexityProfile (it is a config
          value, not a data signal); returns 0.0 as sentinel.
        - ``oversized_clusters``: ColusterProfile stores only the count; returns a
          list of ``count`` empty dicts as a structural placeholder.
        """
        hist: list[int] = list(self.scoring.score_histogram)

        block_pct: dict = {
            "p50": self.blocking.block_sizes_p50,
            "p95": self.blocking.block_sizes_p95,
            "p99": self.blocking.block_sizes_p99,
            "max": self.blocking.block_sizes_max,
        }

        cluster_pct: dict = {
            "p50": self.cluster.cluster_size_p50,
            "p95": 0,
            "p99": self.cluster.cluster_size_p99,
            "max": self.cluster.cluster_size_max,
            "count": self.cluster.n_clusters,
        }

        # Structural placeholder: callers that read oversized_clusters items by
        # key (cluster_id / size / bottleneck_pair) won't find them here, but
        # the count is preserved so length-based guards keep working.
        oversized: list[dict] = [{} for _ in range(self.cluster.oversized_cluster_count)]

        return {
            "score_histogram": hist,
            "blocking_recall": self.blocking.reduction_ratio,
            "block_size_percentiles": block_pct,
            "threshold_overlap_pct": self.scoring.mass_in_borderline,
            "total_pairs_scored": self.scoring.n_pairs_scored,
            "current_threshold": 0.0,
            "preliminary_cluster_sizes": cluster_pct,
            "oversized_clusters": oversized,
            "random_pair_above_threshold_rate": self.scoring.random_pair_above_threshold_rate,
        }
