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
    score_histogram: list[int] = field(default_factory=lambda: [0] * 20)
    dip_statistic: float = 0.0
    mass_above_threshold: float = 0.0
    mass_in_borderline: float = 0.0
    per_field_score_variance: dict[str, float] = field(default_factory=dict)

    def health(self) -> HealthVerdict:
        if self.mass_above_threshold == 0.0:
            return HealthVerdict.RED
        if self.dip_statistic < 0.005:
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
