"""Chao1 scale-aware cardinality for FieldStats + MatchkeyProfile.health().

v24 QIS telemetry (2026-05-29) showed `matchkey` sub-profile YELLOW from
iter 0 to iter 4 with the matchkey-demote rule firing on `first_name`
(sample cardinality 0.997, true full-scale cardinality 0.20). Cause: the
controller's small sample sees almost-unique values on shapes with many
small clusters (QIS: 2M clusters * 5 rows, ~3K sample sees ~1 rep/cluster).

Chao1 mark-recapture estimates full-data uniques from sample stats:
    S* = S + F1^2 / (2 * (F2 + 1))
Then full cardinality = S* / n_full_rows. The +1 on F2 is a small bias
correction that also dodges division by zero when no doubletons exist.
"""
from __future__ import annotations

from goldenmatch.core.complexity_profile import (
    DataProfile,
    FieldStats,
    HealthVerdict,
    MatchkeyProfile,
)


class TestEstimatedFullCardinality:
    def test_backward_compat_no_chao1_inputs_returns_raw(self):
        """FieldStats without Chao1 inputs (pre-2026-05-29 callers) returns
        the raw post_transform_cardinality_ratio."""
        fs = FieldStats(
            post_transform_cardinality_ratio=0.997,
            post_transform_null_rate=0.0,
            post_transform_value_length_p50=10,
        )
        assert fs.estimated_full_cardinality(n_full_rows=10_000_000) == 0.997

    def test_qis_shape_first_name_corrected(self):
        """QIS realistic: 3K sample, 2998 unique (cardinality 0.997), almost
        all singletons (F1=2996), few doubletons (F2=1). Chao1 estimates
        full-scale uniques at ~3M -> full cardinality ~0.30. Correctly
        identifies as NOT uniquely identifying at full scale."""
        fs = FieldStats(
            post_transform_cardinality_ratio=2998 / 3000,
            post_transform_null_rate=0.0,
            post_transform_value_length_p50=10,
            sample_n_rows=3000,
            singleton_count=2996,
            doubleton_count=1,
        )
        # Chao1: S=2998 + 2996^2/(2*2) = 2998 + 8,976,016/4 = 2998 + 2,244,004 = ~2.25M
        # full cardinality = 2.25M / 10M = ~0.22 (correctly NOT uniquely identifying)
        est = fs.estimated_full_cardinality(n_full_rows=10_000_000)
        assert 0.10 < est < 0.30, f"Chao1 estimate {est} should be in [0.10, 0.30] for QIS first_name"

    def test_truly_unique_field_at_small_sample_correctly_uncertain(self):
        """A truly-unique field (e.g., sequential ID) on a 3K sample of 10M
        rows: Chao1 has insufficient evidence to confirm uniqueness, so it
        gives a conservative estimate well below 1.0. This is correct
        behavior -- a 3K sample CANNOT distinguish a truly-unique field
        from one with many repetitions we haven't sampled yet."""
        fs = FieldStats(
            post_transform_cardinality_ratio=1.0,
            post_transform_null_rate=0.0,
            post_transform_value_length_p50=8,
            sample_n_rows=3000,
            singleton_count=3000,
            doubleton_count=0,
        )
        # Chao1: S=3000 + 3000^2/(2*1) = 3000 + 4,500,000 = 4.5M
        # full cardinality = 4.5M / 10M = 0.45
        est = fs.estimated_full_cardinality(n_full_rows=10_000_000)
        assert 0.40 < est < 0.50, f"Chao1 lower bound for truly-unique 3K sample is ~0.45, got {est}"

    def test_caps_estimate_at_one_when_overestimating(self):
        """If Chao1 estimates more uniques than total rows, cap at 1.0."""
        fs = FieldStats(
            post_transform_cardinality_ratio=1.0,
            post_transform_null_rate=0.0,
            post_transform_value_length_p50=8,
            sample_n_rows=1000,
            singleton_count=1000,
            doubleton_count=0,
        )
        # Tiny dataset of n=500 rows would have Chao1 estimate way above 500.
        est = fs.estimated_full_cardinality(n_full_rows=500)
        assert est == 1.0, f"estimate should cap at 1.0 when extrapolated > n_full, got {est}"

    def test_small_dataset_with_clear_repetition_correctly_low(self):
        """1K rows full data, 1K sample (sample is full data), 200 unique
        values with heavy repetition. Chao1 should match the raw
        cardinality (sample IS the full data)."""
        fs = FieldStats(
            post_transform_cardinality_ratio=0.20,
            post_transform_null_rate=0.0,
            post_transform_value_length_p50=10,
            sample_n_rows=1000,
            singleton_count=50,
            doubleton_count=100,
        )
        est = fs.estimated_full_cardinality(n_full_rows=1000)
        # S=200, plus correction term 50^2/(2*101) ~12. Estimate ~212 -> 0.21
        # capped at 1.0 means stays 0.21
        assert 0.20 < est < 0.25, f"Chao1 should stay near raw for full-sample data, got {est}"


class TestMatchkeyProfileHealthWithChao1:
    def _profile_with(self, n_full_rows: int, *, raw_card: float, f1: int, f2: int, n_sample: int):
        return MatchkeyProfile(per_field={
            "field": FieldStats(
                post_transform_cardinality_ratio=raw_card,
                post_transform_null_rate=0.0,
                post_transform_value_length_p50=10,
                sample_n_rows=n_sample,
                singleton_count=f1,
                doubleton_count=f2,
            ),
        })

    def test_qis_shape_now_green_with_chao1(self):
        """The v24 misfire case: raw 0.997 -> Chao1 ~0.22 -> verdict GREEN.
        Previously this returned YELLOW which was wrong (fuzzy scoring at
        full scale produces match candidates fine)."""
        mp = self._profile_with(
            n_full_rows=10_000_000, raw_card=0.997, f1=2996, f2=1, n_sample=3000,
        )
        assert mp.health(n_full_rows=10_000_000) == HealthVerdict.GREEN

    def test_backward_compat_no_n_full_rows_uses_raw_threshold(self):
        """Callers that don't pass n_full_rows (pre-Chao1 behavior) see the
        legacy raw threshold > 0.95 verdict. Same QIS sample returns YELLOW."""
        mp = self._profile_with(
            n_full_rows=10_000_000, raw_card=0.997, f1=2996, f2=1, n_sample=3000,
        )
        assert mp.health() == HealthVerdict.YELLOW

    def test_genuinely_uniform_field_still_red(self):
        """Cardinality 0.0 (every value identical) is still RED regardless
        of Chao1 -- this is a real signal: no discriminative power."""
        mp = MatchkeyProfile(per_field={
            "f": FieldStats(0.0, 0.0, 10),
        })
        assert mp.health(n_full_rows=10_000_000) == HealthVerdict.RED

    def test_full_sample_high_cardinality_still_yellow(self):
        """When the sample IS the full data and shows true high cardinality,
        the verdict stays YELLOW (this is the original intent of the
        signal -- exact matching can't merge anything)."""
        mp = self._profile_with(
            n_full_rows=1000, raw_card=0.99, f1=980, f2=10, n_sample=1000,
        )
        # Chao1: S=990 + 980^2/(2*11) = 990 + 43644 -> capped at 1.0
        verdict = mp.health(n_full_rows=1000)
        assert verdict == HealthVerdict.YELLOW


class TestComplexityProfileHealthThreads:
    """ComplexityProfile.health() should pass data.n_rows to matchkey.health()
    so the Chao1 correction kicks in automatically wherever the rollup verdict
    is consulted (controller commit, rule dispatch, telemetry)."""

    def test_rollup_uses_chao1_when_sample_qis_shape(self):
        from goldenmatch.core.complexity_profile import (
            BlockingProfile,
            ClusterProfile,
            ComplexityProfile,
            DomainProfile,
            ScoringProfile,
        )
        p = ComplexityProfile(
            data=DataProfile(
                n_rows=10_000_000, n_cols=8,
                column_types={"a": "name", "b": "numeric"},
            ),
            domain=DomainProfile(),
            matchkey=MatchkeyProfile(per_field={
                "first_name": FieldStats(
                    post_transform_cardinality_ratio=0.997,
                    post_transform_null_rate=0.0,
                    post_transform_value_length_p50=10,
                    sample_n_rows=3000,
                    singleton_count=2996,
                    doubleton_count=1,
                ),
            }),
            blocking=BlockingProfile(
                keys_used=[["a"]], n_blocks=10, total_comparisons=500,
                reduction_ratio=0.95, block_sizes_p50=10, block_sizes_p95=15,
                block_sizes_p99=20, block_sizes_max=25,
            ),
            scoring=ScoringProfile(
                n_pairs_scored=500, score_histogram=[0]*15 + [100]*5,
                dip_statistic=0.05, mass_above_threshold=0.4,
                mass_in_borderline=0.05, per_field_score_variance={"a": 0.3},
            ),
            cluster=ClusterProfile(
                n_clusters=20, cluster_size_p50=2, cluster_size_p99=5,
                cluster_size_max=8, transitivity_rate=0.95,
                edge_confidence_p50=0.85, edge_confidence_min=0.7,
                oversized_cluster_count=0,
            ),
        )
        # matchkey was YELLOW pre-Chao1; with Chao1 applied via the rollup,
        # matchkey becomes GREEN and the whole rollup is GREEN.
        assert p.health() == HealthVerdict.GREEN
