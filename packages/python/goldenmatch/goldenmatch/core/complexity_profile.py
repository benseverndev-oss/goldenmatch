"""Stage-specific complexity sub-profiles + rollup, emitted by instrumented
pipeline stages and consumed by AutoConfigController + RefitPolicy.

Spec: docs/superpowers/specs/2026-05-06-autoconfig-introspective-controller-design.md §Types & contracts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    """Per-dataset profile. `n_rows` is the size of whatever DataFrame was
    profiled -- on the controller's per-iteration emission that's the
    SAMPLE, not the full dataset.

    `n_full_rows` (added 2026-05-29) is the full dataset's row count when
    the controller has it (i.e., when this profile was emitted from a
    sample but the controller knows the total). Chao1 extrapolation in
    `MatchkeyProfile.health()` and `rule_matchkey_demote_high_cardinality_
    field` needs the full row count, not the sample row count, to
    correctly extrapolate sample-scale singleton/doubleton evidence to
    full-data cardinality.

    Falls back to `n_rows` when not set, preserving pre-2026-05-29 behavior.
    """
    _version: int = 1
    n_rows: int = 0
    n_cols: int = 0
    column_types: dict[str, ColumnType] = field(default_factory=dict)
    cardinality_ratio: dict[str, float] = field(default_factory=dict)
    null_rate: dict[str, float] = field(default_factory=dict)
    value_length_p50: dict[str, int] = field(default_factory=dict)
    value_length_p99: dict[str, int] = field(default_factory=dict)
    column_priors: dict[str, ColumnPrior] | None = None
    n_full_rows: int | None = None

    @property
    def effective_n_rows(self) -> int:
        """Full-dataset row count when the controller threaded it through;
        otherwise the local `n_rows` (which on a non-controller emission IS
        the full data)."""
        return self.n_full_rows if self.n_full_rows is not None else self.n_rows

    def health(self) -> HealthVerdict:
        """RED for empty data, YELLOW for single-column inputs, GREEN otherwise.

        Historical (pre-2026-05-29): also returned YELLOW when every column
        shared the same `column_types` value -- the intent was to flag "all
        your columns are strings, you might want richer typing." In practice
        that's the shape of MOST CSV inputs (every cell parses as text until
        a typed downstream step says otherwise), so it tripped almost every
        real dataset INCLUDING the QIS realistic fixture which produces a
        clean F1=0.9886. v23 telemetry (#577) showed this signal stayed
        YELLOW for all 5 controller iterations with no rule addressing it
        because the verdict isn't actionable -- there's no config change
        that fixes "your data is all strings." Removing the uniform-types
        clause turns this from a noisy false-positive into a precise signal
        for the genuinely-degenerate single-column case.
        """
        if self.n_rows == 0:
            return HealthVerdict.RED
        if self.n_cols == 1:
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
    """Per-field post-transform stats. The raw cardinality ratio is computed
    on whatever sample the caller had; for the controller's sample at 10M
    rows that's 2K-20K rows. Sample-scale cardinality is unreliable for
    full-data behavior on shapes where most clusters are unrepresented in
    the sample (QIS realistic: 2M clusters * 5 rows, 3K controller sample
    -> almost every sampled value is unique, raw cardinality ~0.997, but
    full-data cardinality is 0.20).

    `sample_n_rows`, `singleton_count`, `doubleton_count` are optional
    Chao1 inputs added 2026-05-29 to estimate full-data cardinality from
    the sample. When all three are present `estimated_full_cardinality`
    returns the Chao1 estimate; otherwise it returns the raw ratio
    (preserves pre-Chao1 callers that only populate the three original
    fields).
    """
    post_transform_cardinality_ratio: float
    post_transform_null_rate: float
    post_transform_value_length_p50: int
    # Chao1 mark-recapture inputs. Optional for backward compat.
    sample_n_rows: int | None = None
    singleton_count: int | None = None  # values seen exactly once in sample (F1)
    doubleton_count: int | None = None  # values seen exactly twice in sample (F2)

    def estimated_full_cardinality(self, n_full_rows: int) -> float:
        """Chao1 estimate of full-data cardinality from sample stats.

        Chao1: estimated full unique count S* = S + F1^2 / (2*(F2 + 1))
        Then full cardinality = S* / n_full_rows, capped at 1.0.

        +1 on F2 is a small bias correction that also dodges division by
        zero when no doubletons exist (which itself signals an
        under-sampled distribution -- everything seen once, nothing seen
        twice -- where Chao1 has to extrapolate aggressively).

        Returns the raw post-transform cardinality when Chao1 inputs are
        missing or when n_full_rows <= 0 (matches pre-Chao1 behavior).
        """
        if (
            self.sample_n_rows is None
            or self.singleton_count is None
            or self.doubleton_count is None
            or self.sample_n_rows <= 0
            or n_full_rows <= 0
        ):
            return self.post_transform_cardinality_ratio
        s_sample = int(self.post_transform_cardinality_ratio * self.sample_n_rows)
        estimated_full_unique = s_sample + (
            self.singleton_count * self.singleton_count
        ) / (2 * (self.doubleton_count + 1))
        return min(1.0, estimated_full_unique / n_full_rows)


@dataclass(frozen=True)
class MatchkeyProfile:
    _version: int = 1
    per_field: dict[str, FieldStats] = field(default_factory=dict)

    def health(self, n_full_rows: int | None = None) -> HealthVerdict:
        """RED if any field collapses to a single value (no discrimination).
        YELLOW if any field has near-unique values that scoring can't merge.
        GREEN otherwise.

        n_full_rows: when provided AND the FieldStats carry Chao1 inputs,
        use Chao1-estimated full-data cardinality for the YELLOW check
        instead of the raw sample ratio. v24 QIS telemetry (2026-05-29)
        showed every field's sample cardinality > 0.95 even when full-data
        cardinality was 0.20 (2M clusters of 5 rows, 3K sample sees ~1 rep
        per cluster). The raw verdict produced persistent matchkey YELLOW
        for fixtures where dedupe achieved F1=0.9886 -- the signal was a
        measurement artifact, not a quality problem. Chao1 corrects this.

        Backward compat: n_full_rows=None (or FieldStats without Chao1
        inputs) falls back to the raw post_transform_cardinality_ratio,
        preserving pre-2026-05-29 verdict behavior.
        """
        verdicts = []
        for fs in self.per_field.values():
            if fs.post_transform_cardinality_ratio == 0.0:
                verdicts.append(HealthVerdict.RED)
                continue
            effective_cardinality = (
                fs.estimated_full_cardinality(n_full_rows)
                if n_full_rows is not None
                else fs.post_transform_cardinality_ratio
            )
            if effective_cardinality > 0.95:
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
        # YELLOW only when the borderline band outweighs the above-threshold
        # tail. The legacy `mass_in_borderline > 0.3` rule fired YELLOW on any
        # noisy-but-correct shape (heavy-typo person data lands 30%+ of
        # scored pairs in a 0.2-wide band around the threshold by
        # construction; F1 stays >0.98). Tie the verdict to separation
        # instead: when above-tail mass dominates the borderline band, the
        # config is fine; only flip YELLOW when borderline genuinely
        # swamps confident matches.
        if self.mass_in_borderline > 0.3 and self.mass_in_borderline > self.mass_above_threshold:
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
    # Measured cluster-graph bridge structure (true articulation/bridge
    # detection on the in-memory path). ``bridge_edge_count`` is the raw count of
    # severe bridges (an edge whose removal splits a cluster into two >=2-node
    # parts); ``measured_bridge_risk`` is the fraction of measurable multi-member
    # clusters that contain one. ``None`` when no cluster was small enough to
    # measure cheaply (e.g. distributed paths) -> zero_label falls back to a proxy.
    bridge_edge_count: int = 0
    measured_bridge_risk: float | None = None

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
class ZeroLabelConfidenceProfile:
    """ZeroER-inspired, label-free plausibility of an ER config.

    Computed deterministically from already-emitted ComplexityProfile
    aggregates — no labels, no extra data scans. Design:
    ``docs/design/2026-05-25-zero-label-confidence-autoconfig-design.md``.
    Phase-1 fields derive from current signals; Phase-2 fields stay ``None``
    until their instrumentation / extra-run machinery lands.
    """
    _version: int = 1
    # Phase 1 — derived from emitted signals today.
    latent_separation: float = 0.0
    distribution_overlap: float = 0.0
    score_entropy: float = 0.0
    bimodality_or_dip_score: float = 0.0
    random_pair_contamination: float = 0.0
    transitive_coherence: float = 1.0
    cluster_size_risk: float = 0.0
    overall_confidence: float = 0.0
    confidence_reasons: tuple[str, ...] = ()
    # Phase 2 — None until instrumented / env-gated.
    cluster_bridge_risk: float | None = None
    perturbation_stability: float | None = None
    expected_precision_proxy: float | None = None
    expected_recall_proxy: float | None = None


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
    # Zero-label (ZeroER-inspired) confidence. None until computed by the
    # controller; additive — does NOT participate in health() or
    # normalized_signal_vector() in Phase 1 (no behavioral change).
    zero_label: ZeroLabelConfidenceProfile | None = None

    def health(self) -> HealthVerdict:
        # Use data.effective_n_rows (= n_full_rows when threaded by the
        # controller, n_rows otherwise) for Chao1 extrapolation. blocking
        # and cluster health() existed before Chao1 and keep using n_rows
        # to preserve their meaning ("rows in the profiled DataFrame").
        return _max_severity(
            self.data.health(),
            self.domain.health(),
            self.matchkey.health(n_full_rows=self.data.effective_n_rows),
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
            # Additive: zero-label confidence sub-dict (None until computed).
            "zero_label": asdict(self.zero_label) if self.zero_label is not None else None,
        }
