import pytest
from goldenmatch.core.complexity_profile import (
    HealthVerdict, BlockingProfile, ScoringProfile, ClusterProfile,
    DataProfile, DomainProfile, MatchkeyProfile, ProfileMeta,
    ComplexityProfile, FieldStats,
)


def _green_data():
    return DataProfile(
        n_rows=100, n_cols=4,
        column_types={"a": "text", "b": "id-like", "c": "text", "d": "date"},
        cardinality_ratio={"a": 0.5, "b": 0.99, "c": 0.5, "d": 0.4},
        null_rate={"a": 0, "b": 0, "c": 0, "d": 0},
        value_length_p50={"a": 10}, value_length_p99={"a": 40},
    )


def _green_blocking():
    return BlockingProfile(
        keys_used=[["a"]], n_blocks=10, total_comparisons=500,
        reduction_ratio=0.95, block_sizes_p50=10, block_sizes_p95=15,
        block_sizes_p99=20, block_sizes_max=25,
        singleton_block_count=0, oversized_block_count=0,
    )


def _green_scoring():
    return ScoringProfile(
        n_pairs_scored=500, score_histogram=[0] * 15 + [100] * 5,
        dip_statistic=0.05, mass_above_threshold=0.4,
        mass_in_borderline=0.05, per_field_score_variance={"a": 0.3},
    )


def _green_cluster():
    return ClusterProfile(
        n_clusters=20, cluster_size_p50=2, cluster_size_p99=5,
        cluster_size_max=8, transitivity_rate=0.95,
        edge_confidence_p50=0.85, edge_confidence_min=0.7,
        oversized_cluster_count=0,
    )


def _green_matchkey():
    return MatchkeyProfile(per_field={"a": FieldStats(0.5, 0.0, 10)})


def _green_profile():
    return ComplexityProfile(
        data=_green_data(),
        domain=DomainProfile(detected_domain=None, confidence=0.0, derived_columns=[],
                             low_confidence_row_count=0),
        matchkey=_green_matchkey(),
        blocking=_green_blocking(),
        scoring=_green_scoring(),
        cluster=_green_cluster(),
    )


def test_data_red_when_zero_rows():
    assert DataProfile(n_rows=0, n_cols=4).health() == HealthVerdict.RED


def test_data_yellow_when_one_col():
    assert DataProfile(n_rows=10, n_cols=1, column_types={"a": "text"}).health() == HealthVerdict.YELLOW


def test_domain_yellow_when_low_conf_with_derived_cols():
    dp = DomainProfile(detected_domain="bibliographic", confidence=0.2,
                       derived_columns=["__title_key__"], low_confidence_row_count=10)
    assert dp.health() == HealthVerdict.YELLOW


def test_domain_green_when_low_conf_no_derived():
    dp = DomainProfile(detected_domain=None, confidence=0.0, derived_columns=[],
                       low_confidence_row_count=0)
    assert dp.health() == HealthVerdict.GREEN


def test_matchkey_red_when_field_constant():
    mp = MatchkeyProfile(per_field={"a": FieldStats(0.0, 0.0, 0)})
    assert mp.health() == HealthVerdict.RED


def test_matchkey_yellow_when_field_nearly_unique():
    mp = MatchkeyProfile(per_field={"a": FieldStats(0.99, 0.0, 10)})
    assert mp.health() == HealthVerdict.YELLOW


def test_blocking_red_when_one_block_dominates():
    bp = BlockingProfile(
        keys_used=[["a"]], n_blocks=10, total_comparisons=500,
        reduction_ratio=0.95, block_sizes_p50=5, block_sizes_p95=15,
        block_sizes_p99=200, block_sizes_max=200,
        singleton_block_count=0, oversized_block_count=0,
    )
    # n_rows / n_blocks = 100/10 = 10; p99=200 > 10 * 10 = 100 → RED
    assert bp.health(n_rows=100) == HealthVerdict.RED


def test_blocking_red_when_reduction_ratio_low():
    bp = BlockingProfile(
        keys_used=[["a"]], n_blocks=2, total_comparisons=4900,
        reduction_ratio=0.01,
        block_sizes_p50=49, block_sizes_p95=49, block_sizes_p99=49,
        block_sizes_max=49, singleton_block_count=0, oversized_block_count=0,
    )
    assert bp.health(n_rows=100) == HealthVerdict.RED


def test_blocking_yellow_when_mostly_singletons():
    bp = BlockingProfile(
        keys_used=[["a"]], n_blocks=10, total_comparisons=10,
        reduction_ratio=0.99,
        block_sizes_p50=1, block_sizes_p95=2, block_sizes_p99=2,
        block_sizes_max=3, singleton_block_count=8, oversized_block_count=0,
    )
    assert bp.health(n_rows=100) == HealthVerdict.YELLOW


def test_blocking_red_when_no_blocks():
    bp = BlockingProfile()
    assert bp.health(n_rows=100) == HealthVerdict.RED


def test_scoring_red_when_nothing_matches():
    sp = ScoringProfile(
        n_pairs_scored=500, score_histogram=[100] * 15 + [0] * 5,
        dip_statistic=0.05, mass_above_threshold=0.0,
        mass_in_borderline=0.05,
    )
    assert sp.health() == HealthVerdict.RED


def test_scoring_red_when_unimodal():
    sp = ScoringProfile(
        n_pairs_scored=500, score_histogram=[20] * 20,
        dip_statistic=0.001,
        mass_above_threshold=0.4, mass_in_borderline=0.05,
    )
    assert sp.health() == HealthVerdict.RED


def test_scoring_yellow_when_borderline_heavy():
    sp = ScoringProfile(
        n_pairs_scored=500, score_histogram=[0] * 14 + [100, 100, 100] + [0] * 3,
        dip_statistic=0.05, mass_above_threshold=0.4, mass_in_borderline=0.5,
    )
    assert sp.health() == HealthVerdict.YELLOW


def test_cluster_red_when_one_giant_cluster():
    cp = ClusterProfile(
        n_clusters=2, cluster_size_p50=2, cluster_size_p99=98,
        cluster_size_max=98, transitivity_rate=0.95,
        edge_confidence_p50=0.8, edge_confidence_min=0.7,
        oversized_cluster_count=1,
    )
    # max=98, n_rows=100 → 0.98 > 0.1 → RED
    assert cp.health(n_rows=100) == HealthVerdict.RED


def test_cluster_red_when_low_transitivity():
    cp = ClusterProfile(
        n_clusters=10, cluster_size_p50=2, cluster_size_p99=5,
        cluster_size_max=8, transitivity_rate=0.5,
    )
    assert cp.health(n_rows=100) == HealthVerdict.RED


def test_cluster_yellow_when_oversized_present():
    cp = ClusterProfile(
        n_clusters=10, cluster_size_p50=2, cluster_size_p99=5,
        cluster_size_max=8, transitivity_rate=0.95,
        oversized_cluster_count=1,
    )
    assert cp.health(n_rows=100) == HealthVerdict.YELLOW


def test_complexity_profile_rollup_red_when_any_red():
    p = ComplexityProfile(
        data=_green_data(),
        domain=DomainProfile(),
        matchkey=_green_matchkey(),
        blocking=BlockingProfile(
            keys_used=[["a"]], n_blocks=2, total_comparisons=4900,
            reduction_ratio=0.01,  # RED
            block_sizes_p50=49, block_sizes_p95=49, block_sizes_p99=49,
            block_sizes_max=49,
        ),
        scoring=_green_scoring(),
        cluster=_green_cluster(),
    )
    assert p.health() == HealthVerdict.RED


def test_complexity_profile_rollup_yellow_when_any_yellow():
    p = ComplexityProfile(
        data=_green_data(),
        domain=DomainProfile(detected_domain="x", confidence=0.1,
                             derived_columns=["__y__"]),  # YELLOW
        matchkey=_green_matchkey(),
        blocking=_green_blocking(),
        scoring=_green_scoring(),
        cluster=_green_cluster(),
    )
    assert p.health() == HealthVerdict.YELLOW


def test_complexity_profile_rollup_green_when_all_green():
    assert _green_profile().health() == HealthVerdict.GREEN


def test_normalized_signal_vector_length_8():
    p = _green_profile()
    v = p.normalized_signal_vector()
    assert len(v) == 8
    assert all(0.0 <= x <= 1.0 for x in v)


def test_dataclasses_are_frozen():
    bp = BlockingProfile()
    with pytest.raises((AttributeError, Exception)):  # FrozenInstanceError or AttributeError
        bp.n_blocks = 5  # type: ignore[misc]


def test_version_field_defaults_to_1():
    assert DataProfile()._version == 1
    assert ScoringProfile()._version == 1
    assert ComplexityProfile()._version == 1
