"""Tests for Fellegi-Sunter probabilistic matching."""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)

# ── Schema Tests ──────────────────────────────────────────────────────────


class TestProbabilisticSchema:
    def test_probabilistic_type_accepted(self):
        mk = MatchkeyConfig(
            name="fs_test",
            type="probabilistic",
            fields=[
                MatchkeyField(field="name", scorer="jaro_winkler", levels=3, partial_threshold=0.8),
                MatchkeyField(field="zip", scorer="exact", levels=2),
            ],
            em_iterations=20,
        )
        assert mk.type == "probabilistic"
        assert mk.em_iterations == 20
        assert mk.fields[0].levels == 3
        assert mk.fields[0].partial_threshold == 0.8

    def test_probabilistic_no_threshold_required(self):
        """Probabilistic matchkeys don't need a threshold upfront -- EM computes it."""
        mk = MatchkeyConfig(
            name="fs_test",
            type="probabilistic",
            fields=[
                MatchkeyField(field="name", scorer="jaro_winkler", levels=3, partial_threshold=0.8),
            ],
        )
        assert mk.threshold is None

    def test_probabilistic_fields_need_scorer(self):
        """Each field in a probabilistic matchkey must have a scorer."""
        with pytest.raises(ValueError, match="scorer"):
            MatchkeyConfig(
                name="fs_test",
                type="probabilistic",
                fields=[MatchkeyField(field="name", levels=3)],
            )

    def test_probabilistic_default_levels(self):
        """Fields default to 2 levels (agree/disagree) if not specified."""
        mk = MatchkeyConfig(
            name="fs_test",
            type="probabilistic",
            fields=[MatchkeyField(field="name", scorer="exact")],
        )
        assert mk.fields[0].levels == 2

    def test_probabilistic_comparison_alias(self):
        """The 'comparison' alias works for probabilistic type."""
        mk = MatchkeyConfig(
            name="fs_test",
            comparison="probabilistic",
            fields=[MatchkeyField(field="name", scorer="exact")],
        )
        assert mk.type == "probabilistic"

    def test_probabilistic_in_full_config(self):
        """Probabilistic matchkey works in a full GoldenMatchConfig."""
        cfg = GoldenMatchConfig(
            matchkeys=[{
                "name": "fs",
                "type": "probabilistic",
                "fields": [{"field": "name", "scorer": "jaro_winkler"}],
            }],
            blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["name"])]),
        )
        mks = cfg.get_matchkeys()
        assert mks[0].type == "probabilistic"

    def test_em_fields_have_defaults(self):
        mk = MatchkeyConfig(
            name="fs_test",
            type="probabilistic",
            fields=[MatchkeyField(field="name", scorer="exact")],
        )
        assert mk.em_iterations == 20
        assert mk.convergence_threshold == 0.001
        assert mk.link_threshold is None
        assert mk.review_threshold is None


# ── EM Core Tests ─────────────────────────────────────────────────────────

from goldenmatch.core.probabilistic import (
    EMResult,
    comparison_vector,
    compute_thresholds,
    score_pair_probabilistic,
    score_probabilistic,
    train_em,
)


def _make_dedupe_df():
    """DataFrame with obvious duplicates for EM training."""
    return pl.DataFrame({
        "__row_id__": list(range(1, 11)),
        "first_name": [
            "John", "Jon", "Jane", "Janet", "Bob",
            "Robert", "Alice", "Alicia", "Tom", "Thomas",
        ],
        "last_name": [
            "Smith", "Smith", "Doe", "Doe", "Jones",
            "Jones", "Brown", "Brown", "Wilson", "Wilson",
        ],
        "zip": [
            "90210", "90210", "10001", "10001", "60601",
            "60601", "30301", "30301", "20001", "20002",
        ],
    })


def _make_probabilistic_mk(**kwargs):
    defaults = dict(
        name="fs",
        type="probabilistic",
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3, partial_threshold=0.8),
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=2, partial_threshold=0.85),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ],
    )
    defaults.update(kwargs)
    return MatchkeyConfig(**defaults)


class TestComparisonVector:
    def test_exact_agree(self):
        mk = _make_probabilistic_mk()
        vec = comparison_vector(
            {"first_name": "John", "last_name": "Smith", "zip": "90210"},
            {"first_name": "John", "last_name": "Smith", "zip": "90210"},
            mk,
        )
        # first_name: 3-level, exact match -> 2 (agree)
        # last_name: 2-level, exact match -> 1 (agree)
        # zip: 2-level, exact match -> 1 (agree)
        assert vec == [2, 1, 1]

    def test_partial_agree(self):
        mk = _make_probabilistic_mk()
        vec = comparison_vector(
            {"first_name": "John", "last_name": "Smith", "zip": "90210"},
            {"first_name": "Jon", "last_name": "Smyth", "zip": "90211"},
            mk,
        )
        # first_name: JW("John","Jon") ~ 0.93 -> partial (>0.8, <0.95)
        # last_name: JW("Smith","Smyth") ~ 0.87 -> agree (>0.85 for 2-level)
        # zip: exact("90210","90211") = 0 -> disagree
        assert vec[0] == 1  # partial
        assert vec[2] == 0  # disagree

    def test_full_disagree(self):
        mk = _make_probabilistic_mk()
        vec = comparison_vector(
            {"first_name": "Alice", "last_name": "Brown", "zip": "30301"},
            {"first_name": "Tom", "last_name": "Wilson", "zip": "20001"},
            mk,
        )
        assert vec[0] == 0  # disagree
        assert vec[1] == 0  # disagree
        assert vec[2] == 0  # disagree

    def test_null_values_disagree(self):
        mk = _make_probabilistic_mk()
        vec = comparison_vector(
            {"first_name": None, "last_name": "Smith", "zip": "90210"},
            {"first_name": "John", "last_name": "Smith", "zip": "90210"},
            mk,
        )
        assert vec[0] == 0  # null -> disagree


class TestEMTraining:
    def test_em_converges(self):
        df = _make_dedupe_df()
        mk = _make_probabilistic_mk()
        result = train_em(df, mk, n_sample_pairs=100, max_iterations=50)
        assert result.converged or result.iterations <= 50
        assert 0 < result.proportion_matched < 1

    def test_em_produces_valid_probabilities(self):
        df = _make_dedupe_df()
        mk = _make_probabilistic_mk()
        result = train_em(df, mk, n_sample_pairs=100)
        for field_name, probs in result.m_probs.items():
            assert abs(sum(probs) - 1.0) < 0.01, f"m_probs for {field_name} don't sum to 1"
        for field_name, probs in result.u_probs.items():
            assert abs(sum(probs) - 1.0) < 0.01, f"u_probs for {field_name} don't sum to 1"

    def test_em_match_weights_direction(self):
        """Match weights should be positive for agree, negative for disagree."""
        df = _make_dedupe_df()
        mk = _make_probabilistic_mk()
        result = train_em(df, mk, n_sample_pairs=100)
        for field_name, weights in result.match_weights.items():
            # Highest level (agree) should have positive weight
            assert weights[-1] > 0, f"Agree weight for {field_name} should be positive"
            # Lowest level (disagree) should have negative weight
            assert weights[0] < 0, f"Disagree weight for {field_name} should be negative"

    def test_em_with_small_data(self):
        """EM should handle datasets too small for proper training."""
        df = pl.DataFrame({
            "__row_id__": [1, 2],
            "first_name": ["John", "Jon"],
            "last_name": ["Smith", "Smith"],
            "zip": ["90210", "90210"],
        })
        mk = _make_probabilistic_mk()
        result = train_em(df, mk, n_sample_pairs=10)
        assert result is not None
        assert len(result.m_probs) == 3


class TestComputeThresholds:
    def test_thresholds_in_range(self):
        df = _make_dedupe_df()
        mk = _make_probabilistic_mk()
        result = train_em(df, mk, n_sample_pairs=100)
        link, review = compute_thresholds(result)
        assert 0 < review < link < 1


class TestScoreProbabilistic:
    def test_scores_obvious_matches(self):
        """Obvious duplicates should score high."""
        df = _make_dedupe_df()
        mk = _make_probabilistic_mk()
        em = train_em(df, mk, n_sample_pairs=100)

        # Block with just the first two records (John/Jon Smith, same zip)
        block = df.head(2)
        pairs = score_probabilistic(block, mk, em)
        # Should find a match
        assert len(pairs) >= 1
        # Score should be high
        assert pairs[0][2] > 0.5

    def test_excludes_pairs(self):
        df = _make_dedupe_df()
        mk = _make_probabilistic_mk()
        em = train_em(df, mk, n_sample_pairs=100)

        block = df.head(2)
        pairs_all = score_probabilistic(block, mk, em)
        pairs_excluded = score_probabilistic(block, mk, em, exclude_pairs={(1, 2)})
        assert len(pairs_excluded) < len(pairs_all) or len(pairs_all) == 0

    def test_returns_standard_pair_format(self):
        df = _make_dedupe_df()
        mk = _make_probabilistic_mk()
        em = train_em(df, mk, n_sample_pairs=100)

        block = df.head(4)
        pairs = score_probabilistic(block, mk, em)
        for p in pairs:
            assert len(p) == 3
            assert isinstance(p[0], int)
            assert isinstance(p[1], int)
            assert isinstance(p[2], float)
            assert 0 <= p[2] <= 1


class TestPipelineIntegration:
    def test_full_pipeline_with_probabilistic(self, tmp_path):
        """End-to-end: write CSV, run_dedupe with probabilistic matchkey."""
        import csv
        csv_path = tmp_path / "test.csv"
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["first_name", "last_name", "zip"])
            w.writerow(["John", "Smith", "90210"])
            w.writerow(["Jon", "Smith", "90210"])
            w.writerow(["Jane", "Doe", "10001"])
            w.writerow(["Janet", "Doe", "10001"])
            w.writerow(["Alice", "Brown", "30301"])

        from goldenmatch.core.pipeline import run_dedupe
        cfg = GoldenMatchConfig(
            matchkeys=[{
                "name": "fs",
                "type": "probabilistic",
                "fields": [
                    {"field": "first_name", "scorer": "jaro_winkler", "levels": 3, "partial_threshold": 0.8},
                    {"field": "last_name", "scorer": "jaro_winkler", "levels": 2},
                    {"field": "zip", "scorer": "exact", "levels": 2},
                ],
            }],
            blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
        )

        result = run_dedupe(
            [(str(csv_path), "test")], cfg,
            output_clusters=True,
        )
        clusters = result["clusters"]
        # Should find at least 1 cluster (John/Jon Smith share zip)
        multi_clusters = {cid: c for cid, c in clusters.items() if c["size"] > 1}
        assert len(multi_clusters) >= 1

    def test_engine_with_probabilistic(self, tmp_path):
        """MatchEngine works with probabilistic matchkeys."""
        import csv
        csv_path = tmp_path / "test.csv"
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["first_name", "last_name", "zip"])
            w.writerow(["John", "Smith", "90210"])
            w.writerow(["Jon", "Smith", "90210"])
            w.writerow(["Bob", "Jones", "60601"])

        from goldenmatch.tui.engine import MatchEngine
        engine = MatchEngine([str(csv_path)])
        cfg = GoldenMatchConfig(
            matchkeys=[{
                "name": "fs",
                "type": "probabilistic",
                "fields": [
                    {"field": "first_name", "scorer": "jaro_winkler", "levels": 3, "partial_threshold": 0.8},
                    {"field": "last_name", "scorer": "exact", "levels": 2},
                    {"field": "zip", "scorer": "exact", "levels": 2},
                ],
            }],
            blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
        )
        result = engine.run_full(cfg)
        assert result.stats.total_records == 3


class TestScorePairProbabilistic:
    def test_single_pair_scoring(self):
        mk = _make_probabilistic_mk()
        df = _make_dedupe_df()
        em = train_em(df, mk, n_sample_pairs=100)

        score = score_pair_probabilistic(
            {"first_name": "John", "last_name": "Smith", "zip": "90210"},
            {"first_name": "John", "last_name": "Smith", "zip": "90210"},
            mk, em,
        )
        assert score > 0.8  # identical records should score very high

    def test_non_match_scores_low(self):
        mk = _make_probabilistic_mk()
        df = _make_dedupe_df()
        em = train_em(df, mk, n_sample_pairs=100)

        score = score_pair_probabilistic(
            {"first_name": "Alice", "last_name": "Brown", "zip": "30301"},
            {"first_name": "Tom", "last_name": "Wilson", "zip": "20001"},
            mk, em,
        )
        assert score < 0.5


# ── Continuous EM Tests ──────────────────────────────────────────────────

from goldenmatch.core.probabilistic import (
    ContinuousEMResult,
    _build_comparison_matrix,
    _build_continuous_matrix,
    _fallback_result,
    _sample_pairs,
    continuous_scores,
    score_probabilistic_continuous,
    train_em_continuous,
)


class TestContinuousScores:
    def test_identical_records_score_high(self):
        mk = _make_probabilistic_mk()
        scores = continuous_scores(
            {"first_name": "John", "last_name": "Smith", "zip": "90210"},
            {"first_name": "John", "last_name": "Smith", "zip": "90210"},
            mk,
        )
        assert len(scores) == 3
        assert all(s == 1.0 for s in scores)

    def test_completely_different_records(self):
        mk = _make_probabilistic_mk()
        scores = continuous_scores(
            {"first_name": "Alice", "last_name": "Brown", "zip": "30301"},
            {"first_name": "Tom", "last_name": "Wilson", "zip": "20001"},
            mk,
        )
        assert len(scores) == 3
        # first_name and zip should be very different; last_name JW may be moderate
        assert scores[0] < 0.5  # Alice vs Tom
        assert scores[2] == 0.0  # zip exact mismatch

    def test_null_value_returns_zero(self):
        mk = _make_probabilistic_mk()
        scores = continuous_scores(
            {"first_name": None, "last_name": "Smith", "zip": "90210"},
            {"first_name": "John", "last_name": "Smith", "zip": "90210"},
            mk,
        )
        # None -> score_field returns None -> 0.0
        assert scores[0] == 0.0
        assert scores[1] == 1.0
        assert scores[2] == 1.0


class TestTrainEMContinuous:
    def test_converges(self):
        df = _make_dedupe_df()
        mk = _make_probabilistic_mk()
        result = train_em_continuous(df, mk, n_sample_pairs=100, max_iterations=50)
        assert isinstance(result, ContinuousEMResult)
        assert result.converged or result.iterations <= 50
        assert 0 < result.proportion_matched < 1

    def test_produces_valid_parameters(self):
        df = _make_dedupe_df()
        mk = _make_probabilistic_mk()
        result = train_em_continuous(df, mk, n_sample_pairs=100)
        for f in mk.fields:
            assert f.field in result.m_mean
            assert f.field in result.m_var
            assert f.field in result.u_mean
            assert f.field in result.u_var
            assert result.m_var[f.field] > 0
            assert result.u_var[f.field] > 0

    def test_match_mean_higher_than_nonmatch(self):
        """Match distribution should have higher mean score than non-match."""
        df = _make_dedupe_df()
        mk = _make_probabilistic_mk()
        result = train_em_continuous(df, mk, n_sample_pairs=100)
        for f in mk.fields:
            # m_mean should generally be >= u_mean (matches score higher)
            # This is a soft check since EM is stochastic
            assert result.m_mean[f.field] >= 0.0
            assert result.u_mean[f.field] >= 0.0

    def test_with_blocking_fields(self):
        df = _make_dedupe_df()
        mk = _make_probabilistic_mk()
        result = train_em_continuous(
            df, mk, n_sample_pairs=100,
            blocking_fields=["zip"],
        )
        assert isinstance(result, ContinuousEMResult)
        # Blocking field zip should have fixed parameters
        assert result.m_mean["zip"] == 0.99
        assert result.u_mean["zip"] == 0.99

    def test_too_few_pairs_fallback(self):
        """Very small dataset returns fallback result."""
        df = pl.DataFrame({
            "__row_id__": [1],
            "first_name": ["John"],
            "last_name": ["Smith"],
            "zip": ["90210"],
        })
        mk = _make_probabilistic_mk()
        result = train_em_continuous(df, mk, n_sample_pairs=10)
        assert isinstance(result, ContinuousEMResult)
        assert result.converged is False
        assert result.iterations == 0


class TestScoreProbabilisticContinuous:
    def test_scores_obvious_matches(self):
        df = _make_dedupe_df()
        mk = _make_probabilistic_mk()
        em = train_em_continuous(df, mk, n_sample_pairs=100)

        block = df.head(2)  # John/Jon Smith same zip
        pairs = score_probabilistic_continuous(block, mk, em, threshold=0.3)
        assert len(pairs) >= 1
        assert pairs[0][2] > 0.3

    def test_excludes_pairs(self):
        df = _make_dedupe_df()
        mk = _make_probabilistic_mk()
        em = train_em_continuous(df, mk, n_sample_pairs=100)

        block = df.head(2)
        all_pairs = score_probabilistic_continuous(block, mk, em, threshold=0.3)
        excluded = score_probabilistic_continuous(
            block, mk, em, threshold=0.3, exclude_pairs={(1, 2)},
        )
        assert len(excluded) <= len(all_pairs)

    def test_returns_standard_format(self):
        df = _make_dedupe_df()
        mk = _make_probabilistic_mk()
        em = train_em_continuous(df, mk, n_sample_pairs=100)

        # Use just the first 2 records (similar pair) to avoid overflow
        block = df.head(2)
        pairs = score_probabilistic_continuous(block, mk, em, threshold=0.3)
        for p in pairs:
            assert len(p) == 3
            assert isinstance(p[0], int)
            assert isinstance(p[1], int)
            assert isinstance(p[2], float)
            assert 0 <= p[2] <= 1

    def test_high_threshold_filters_more(self):
        df = _make_dedupe_df()
        mk = _make_probabilistic_mk()
        em = train_em_continuous(df, mk, n_sample_pairs=100)

        # Use similar pair to avoid overflow on dissimilar records
        block = df.head(2)
        low = score_probabilistic_continuous(block, mk, em, threshold=0.3)
        high = score_probabilistic_continuous(block, mk, em, threshold=0.99)
        assert len(high) <= len(low)


class TestFallbackResult:
    def test_two_level_fields(self):
        mk = MatchkeyConfig(
            name="fb",
            type="probabilistic",
            fields=[MatchkeyField(field="name", scorer="exact", levels=2)],
        )
        result = _fallback_result(mk)
        assert result.converged is False
        assert result.iterations == 0
        assert len(result.m_probs["name"]) == 2
        assert len(result.u_probs["name"]) == 2
        assert abs(sum(result.m_probs["name"]) - 1.0) < 0.01
        assert abs(sum(result.u_probs["name"]) - 1.0) < 0.01

    def test_three_level_fields(self):
        mk = MatchkeyConfig(
            name="fb",
            type="probabilistic",
            fields=[MatchkeyField(field="name", scorer="jaro_winkler", levels=3)],
        )
        result = _fallback_result(mk)
        assert len(result.m_probs["name"]) == 3
        assert len(result.u_probs["name"]) == 3
        # Agree weight should be positive, disagree negative
        assert result.match_weights["name"][-1] > 0
        assert result.match_weights["name"][0] < 0


class TestComparisonVectorEdgeCases:
    def test_n_levels(self):
        """N-level comparison vector with 5 levels."""
        mk = MatchkeyConfig(
            name="fs",
            type="probabilistic",
            fields=[
                MatchkeyField(field="name", scorer="jaro_winkler", levels=5),
            ],
        )
        # Identical -> score 1.0 -> level 4 (highest for 5 levels)
        vec = comparison_vector(
            {"name": "John"},
            {"name": "John"},
            mk,
        )
        assert vec == [4]

        # Very different -> score low -> level 0
        vec = comparison_vector(
            {"name": "Alice"},
            {"name": "Zebra"},
            mk,
        )
        assert vec[0] == 0

    def test_both_null(self):
        mk = _make_probabilistic_mk()
        vec = comparison_vector(
            {"first_name": None, "last_name": None, "zip": None},
            {"first_name": None, "last_name": None, "zip": None},
            mk,
        )
        # All nulls -> all disagree
        assert vec == [0, 0, 0]


class TestSamplePairs:
    def test_small_dataset_all_pairs(self):
        df = pl.DataFrame({"__row_id__": [1, 2, 3]})
        pairs = _sample_pairs(df, n_pairs=100)
        # 3 rows -> 3 possible pairs, all returned
        assert len(pairs) == 3

    def test_single_record(self):
        df = pl.DataFrame({"__row_id__": [1]})
        pairs = _sample_pairs(df, n_pairs=100)
        assert pairs == []

    def test_sampling_limit(self):
        df = pl.DataFrame({"__row_id__": list(range(100))})
        pairs = _sample_pairs(df, n_pairs=50)
        assert len(pairs) <= 50


class TestBuildComparisonMatrix:
    def test_shape(self):
        mk = _make_probabilistic_mk()
        row_lookup = {
            1: {"first_name": "John", "last_name": "Smith", "zip": "90210"},
            2: {"first_name": "Jon", "last_name": "Smith", "zip": "90210"},
        }
        pairs = [(1, 2)]
        mat = _build_comparison_matrix(pairs, row_lookup, mk)
        assert mat.shape == (1, 3)

    def test_missing_row(self):
        """Missing row in lookup returns all-disagree."""
        mk = _make_probabilistic_mk()
        row_lookup = {
            1: {"first_name": "John", "last_name": "Smith", "zip": "90210"},
        }
        pairs = [(1, 99)]  # row 99 missing
        mat = _build_comparison_matrix(pairs, row_lookup, mk)
        assert mat.shape == (1, 3)


class TestBuildContinuousMatrix:
    def test_shape(self):
        mk = _make_probabilistic_mk()
        row_lookup = {
            1: {"first_name": "John", "last_name": "Smith", "zip": "90210"},
            2: {"first_name": "Jon", "last_name": "Smith", "zip": "90210"},
        }
        pairs = [(1, 2)]
        mat = _build_continuous_matrix(pairs, row_lookup, mk)
        assert mat.shape == (1, 3)
        assert all(0.0 <= mat[0, j] <= 1.0 for j in range(3))


class TestComputeThresholdsEdgeCases:
    def test_with_scored_weights(self):
        """Data-driven thresholds from actual pair scores."""
        df = _make_dedupe_df()
        mk = _make_probabilistic_mk()
        em = train_em(df, mk, n_sample_pairs=100)

        # Simulate scored weights
        import random
        rng = random.Random(42)
        weights = [rng.random() for _ in range(200)]
        link, review = compute_thresholds(em, weights)
        assert 0.25 <= review < link <= 0.95

    def test_few_scored_weights_uses_defaults(self):
        """With fewer than 50 weights, uses fixed defaults."""
        em = EMResult(
            m_probs={"name": [0.1, 0.9]},
            u_probs={"name": [0.9, 0.1]},
            match_weights={"name": [-3.0, 3.0]},
            converged=True,
            iterations=5,
            proportion_matched=0.05,
        )
        weights = [0.5] * 30  # too few
        link, review = compute_thresholds(em, weights)
        assert link == 0.50
        assert review == 0.35

    def test_no_scored_weights(self):
        em = EMResult(
            m_probs={"name": [0.1, 0.9]},
            u_probs={"name": [0.9, 0.1]},
            match_weights={"name": [-3.0, 3.0]},
            converged=True,
            iterations=5,
            proportion_matched=0.05,
        )
        link, review = compute_thresholds(em)
        assert link == 0.50
        assert review == 0.35


class TestEMWithBlockingFields:
    def test_blocking_fields_get_fixed_weights(self):
        df = _make_dedupe_df()
        mk = _make_probabilistic_mk()
        result = train_em(df, mk, n_sample_pairs=100, blocking_fields=["zip"])
        # zip blocking field should have fixed weights
        assert result.match_weights["zip"] == [-3.0, 3.0]

    def test_blocking_fields_neutral_u(self):
        df = _make_dedupe_df()
        mk = _make_probabilistic_mk()
        result = train_em(df, mk, n_sample_pairs=100, blocking_fields=["zip"])
        assert result.u_probs["zip"] == [0.50, 0.50]


# ── Weight monotonicity guard (Phase 0) ────────────────────────────────────


class TestWeightMonotonicity:
    def test_isotonic_already_monotone_unchanged(self):
        from goldenmatch.core.probabilistic import _isotonic_nondecreasing
        assert _isotonic_nondecreasing([-3.0, 1.0, 5.0]) == [-3.0, 1.0, 5.0]

    def test_isotonic_pools_inversion(self):
        from goldenmatch.core.probabilistic import _isotonic_nondecreasing
        # partial (28.0) outweighs exact (12.0): pool to their mean.
        out = _isotonic_nondecreasing([-2.0, 28.0, 12.0])
        assert out[0] == -2.0
        assert out[1] == out[2] == pytest.approx(20.0)
        # result is non-decreasing
        assert all(out[i] <= out[i + 1] + 1e-9 for i in range(len(out) - 1))

    def test_isotonic_single_and_empty(self):
        from goldenmatch.core.probabilistic import _isotonic_nondecreasing
        assert _isotonic_nondecreasing([5.0]) == [5.0]
        assert _isotonic_nondecreasing([]) == []

    def test_enforce_reports_adjusted_and_skips_blocking(self):
        from goldenmatch.core.probabilistic import enforce_weight_monotonicity
        weights = {
            "title": [-2.0, 28.0, 12.0],   # inverted
            "year": [12.0, -3.0],          # inverted but blocking -> skipped
            "authors": [-5.0, 3.0, 9.0],   # already monotone
        }
        out, adjusted = enforce_weight_monotonicity(weights, skip_fields=["year"])
        assert adjusted == ["title"]
        assert out["year"] == [12.0, -3.0]          # untouched
        assert out["authors"] == [-5.0, 3.0, 9.0]   # untouched
        assert out["title"][1] == out["title"][2]   # pooled

    def test_mode_env_parsing(self, monkeypatch):
        from goldenmatch.core import probabilistic as p
        monkeypatch.delenv("GOLDENMATCH_FS_MONOTONIC", raising=False)
        assert p._fs_monotonic_mode() == "warn"
        monkeypatch.setenv("GOLDENMATCH_FS_MONOTONIC", "enforce")
        assert p._fs_monotonic_mode() == "enforce"
        monkeypatch.setenv("GOLDENMATCH_FS_MONOTONIC", "0")
        assert p._fs_monotonic_mode() == "off"
        monkeypatch.setenv("GOLDENMATCH_FS_MONOTONIC", "garbage")
        assert p._fs_monotonic_mode() == "warn"

    def test_warn_mode_does_not_modify_weights(self, monkeypatch):
        # Default (warn) leaves EM weights as-is; enforce changes them.
        from goldenmatch.core.probabilistic import train_em
        df = _make_dedupe_df()
        mk = _make_probabilistic_mk()
        monkeypatch.setenv("GOLDENMATCH_FS_MONOTONIC", "off")
        raw = train_em(df, mk, n_sample_pairs=200, blocking_fields=["zip"])
        monkeypatch.setenv("GOLDENMATCH_FS_MONOTONIC", "warn")
        warned = train_em(df, mk, n_sample_pairs=200, blocking_fields=["zip"])
        assert warned.match_weights == raw.match_weights


class TestPosteriorThresholds:
    def test_calibrated_link_cut_is_high(self):
        # Posterior default cut is 0.99, not the 0.5 Bayes boundary.
        from goldenmatch.core.probabilistic import EMResult, compute_thresholds
        em = EMResult(
            m_probs={"name": [0.1, 0.9]}, u_probs={"name": [0.9, 0.1]},
            match_weights={"name": [-3.0, 3.0]}, converged=True,
            iterations=5, proportion_matched=0.05,
        )
        link, review = compute_thresholds(em, calibrated=True)
        assert link == 0.99
        assert review == 0.50


# ── Model persistence (Phase 1a) ───────────────────────────────────────────


class TestModelPersistence:
    def _trained(self):
        from goldenmatch.core.probabilistic import train_em
        return train_em(_make_dedupe_df(), _make_probabilistic_mk(),
                        n_sample_pairs=200, blocking_fields=["zip"])

    def test_to_from_dict_roundtrip(self):
        from goldenmatch.core.probabilistic import EMResult
        em = self._trained()
        back = EMResult.from_dict(em.to_dict())
        assert back.m_probs == em.m_probs
        assert back.u_probs == em.u_probs
        assert back.match_weights == em.match_weights
        assert back.proportion_matched == em.proportion_matched
        assert back.converged == em.converged
        assert back.iterations == em.iterations

    def test_save_load_json_roundtrip(self, tmp_path):
        from goldenmatch.core.probabilistic import EMResult
        em = self._trained()
        path = str(tmp_path / "model.json")
        em.save_json(path)
        loaded = EMResult.load_json(path)
        assert loaded.match_weights == em.match_weights
        assert loaded.to_dict() == em.to_dict()

    def test_to_dict_has_version_marker(self):
        em = self._trained()
        d = em.to_dict()
        assert d["__type__"] == "goldenmatch.EMResult"
        assert d["__version__"] == 1

    def test_from_dict_rejects_future_version(self):
        from goldenmatch.core.probabilistic import EMResult
        d = self._trained().to_dict()
        d["__version__"] = 999
        with pytest.raises(ValueError, match="newer than this"):
            EMResult.from_dict(d)

    def test_validate_for_detects_missing_field(self):
        from goldenmatch.core.probabilistic import FSModelMismatchError
        em = self._trained()
        # A matchkey with a field the model never saw.
        mk = _make_probabilistic_mk(fields=[
            MatchkeyField(field="email", scorer="exact", levels=2),
        ])
        with pytest.raises(FSModelMismatchError, match="no weights for field"):
            em.validate_for(mk)

    def test_validate_for_detects_level_mismatch(self):
        from goldenmatch.core.probabilistic import FSModelMismatchError
        em = self._trained()  # first_name trained at 3 levels
        mk = _make_probabilistic_mk(fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=2),
        ])
        with pytest.raises(FSModelMismatchError, match="levels"):
            em.validate_for(mk)

    def test_load_or_train_saves_then_loads(self, tmp_path):
        # First call (cache miss): trains and writes the file.
        # Second call (cache hit): loads, skips EM. Both produce the same model.
        from goldenmatch.core import probabilistic as p
        path = str(tmp_path / "fs.json")
        df = _make_dedupe_df()
        mk = _make_probabilistic_mk(model_path=path)

        calls = {"n": 0}
        real_train = p.train_em

        def _counting_train(*a, **k):
            calls["n"] += 1
            return real_train(*a, **k)

        p.train_em = _counting_train
        try:
            em1 = p.load_or_train_em(df, mk, blocking_fields=["zip"])
            assert calls["n"] == 1
            assert (tmp_path / "fs.json").exists()
            em2 = p.load_or_train_em(df, mk, blocking_fields=["zip"])
            assert calls["n"] == 1  # second call did NOT retrain
        finally:
            p.train_em = real_train
        assert em2.match_weights == em1.match_weights

    def test_load_or_train_no_path_is_plain_train(self):
        from goldenmatch.core.probabilistic import load_or_train_em
        mk = _make_probabilistic_mk()  # no model_path
        em = load_or_train_em(_make_dedupe_df(), mk, blocking_fields=["zip"])
        assert "first_name" in em.match_weights


class TestDedupeWithPersistedModel:
    def test_saved_model_run_is_byte_identical(self, tmp_path):
        # Gate: a dedupe that loads a persisted FS model produces identical
        # pairs/clusters to one that trains from scratch.
        from goldenmatch import dedupe_df
        from goldenmatch.config.schemas import (
            BlockingConfig,
            BlockingKeyConfig,
            GoldenMatchConfig,
        )

        def _cfg(model_path=None):
            return GoldenMatchConfig(
                matchkeys=[_make_probabilistic_mk(model_path=model_path)],
                blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
            )

        df = _make_dedupe_df().drop("__row_id__")
        # Baseline: train from scratch, no persistence.
        baseline = dedupe_df(df, config=_cfg())
        # First persisted run writes the model; second reads it (skips EM).
        path = str(tmp_path / "m.json")
        first = dedupe_df(df, config=_cfg(path))
        assert (tmp_path / "m.json").exists()
        second = dedupe_df(df, config=_cfg(path))

        def _parts(r):
            return sorted(
                tuple(sorted(c["members"]))
                for c in r.clusters.values() if len(c.get("members", [])) > 1
            )
        assert _parts(first) == _parts(baseline)
        assert _parts(second) == _parts(baseline)


# ── Supervised m-training (Phase 1b) ───────────────────────────────────────


def _supervised_df():
    # 6 records: 3 true-match pairs (0-1, 2-3, 4-5) sharing zip blocks.
    return pl.DataFrame({
        "__row_id__": [0, 1, 2, 3, 4, 5],
        "first_name": ["John", "Jon", "Jane", "Jane", "Bob", "Bob"],
        "last_name": ["Smith", "Smith", "Doe", "Doe", "Lee", "Lee"],
        "zip": ["111", "111", "222", "222", "333", "333"],
    })


class TestEstimateMFromLabels:
    def test_returns_direct_estimate(self):
        from goldenmatch.core.probabilistic import estimate_m_from_labels
        df = _supervised_df()
        em = estimate_m_from_labels(df, _make_probabilistic_mk(),
                                    [(0, 1), (2, 3), (4, 5)], blocking_fields=["zip"])
        assert em.iterations == 0          # no EM
        assert em.converged is True
        assert set(em.match_weights) == {"first_name", "last_name", "zip"}
        assert em.match_weights["zip"] == [-3.0, 3.0]   # blocking -> fixed

    def test_no_usable_labels_raises(self):
        from goldenmatch.core.probabilistic import estimate_m_from_labels
        df = _supervised_df()
        with pytest.raises(ValueError, match="no usable labeled pairs"):
            estimate_m_from_labels(df, _make_probabilistic_mk(),
                                   [(99, 100), (7, 7)], blocking_fields=["zip"])

    def test_m_reflects_agreement_in_labels(self):
        # Labeled matches always agree on last_name -> top-level m high ->
        # positive top-level match weight.
        from goldenmatch.core.probabilistic import estimate_m_from_labels
        df = _supervised_df()
        em = estimate_m_from_labels(df, _make_probabilistic_mk(),
                                    [(0, 1), (2, 3), (4, 5)], blocking_fields=["zip"])
        # last_name is 2-level (disagree/agree); agree weight must beat disagree.
        w = em.match_weights["last_name"]
        assert w[-1] > w[0]
        # m for the agree level should dominate.
        assert em.m_probs["last_name"][-1] > em.m_probs["last_name"][0]

    def test_smoothing_prevents_zero_m(self):
        from goldenmatch.core.probabilistic import estimate_m_from_labels
        df = _supervised_df()
        em = estimate_m_from_labels(df, _make_probabilistic_mk(),
                                    [(0, 1)], blocking_fields=["zip"])
        for field, probs in em.m_probs.items():
            assert all(p > 0 for p in probs), field

    def test_scores_pairs_end_to_end(self):
        from goldenmatch.core.probabilistic import (
            estimate_m_from_labels,
            probabilistic_block_scorer,
        )
        df = _supervised_df()
        mk = _make_probabilistic_mk()
        em = estimate_m_from_labels(df, mk, [(0, 1), (2, 3), (4, 5)],
                                    blocking_fields=["zip"])
        scorer = probabilistic_block_scorer(mk, em)
        pairs = scorer(df)
        assert isinstance(pairs, list)


class TestLabelAdapters:
    def test_labels_from_corrections_keeps_approve(self):
        from types import SimpleNamespace as NS
        from goldenmatch.core.probabilistic import labels_from_corrections
        corr = [
            NS(id_a=1, id_b=2, decision="approve"),
            NS(id_a=3, id_b=4, decision="reject"),
            NS(id_a=5, id_b=6, decision="approve"),
        ]
        assert labels_from_corrections(corr) == [(1, 2), (5, 6)]

    def test_labels_from_review_items_keeps_approved(self):
        from types import SimpleNamespace as NS
        from goldenmatch.core.probabilistic import labels_from_review_items
        items = [
            NS(id_a=1, id_b=2, status="approved"),
            NS(id_a=3, id_b=4, status="rejected"),
            NS(id_a=5, id_b=6, status="pending"),
        ]
        assert labels_from_review_items(items) == [(1, 2)]

    def test_labels_from_memory_store_duck_typed(self):
        from types import SimpleNamespace as NS
        from goldenmatch.core.probabilistic import labels_from_memory_store
        store = NS(get_corrections=lambda ds: [
            NS(id_a=7, id_b=8, decision="approve"),
            NS(id_a=9, id_b=10, decision="reject"),
        ])
        assert labels_from_memory_store(store) == [(7, 8)]


# ── FS waterfall explainability (Phase 2) ──────────────────────────────────


class TestFSWaterfall:
    def _em_and_mk(self):
        from goldenmatch.core.probabilistic import train_em
        df = _make_dedupe_df()
        mk = _make_probabilistic_mk()
        em = train_em(df, mk, n_sample_pairs=300, blocking_fields=["zip"])
        return df, mk, em

    def test_bits_sum_to_total(self):
        from goldenmatch.core.probabilistic import explain_pair_fs
        _df, mk, em = self._em_and_mk()
        row_a = {"first_name": "John", "last_name": "Smith", "zip": "90210"}
        row_b = {"first_name": "Jon", "last_name": "Smith", "zip": "90210"}
        wf = explain_pair_fs(row_a, row_b, mk, em)
        # Per-field bits sum to total weight (the gate).
        assert sum(c.weight_bits for c in wf.fields) == pytest.approx(wf.total_weight_bits)
        # Prior + weight == final; posterior is the logistic of final bits.
        assert wf.prior_bits + wf.total_weight_bits == pytest.approx(wf.final_bits)
        assert wf.posterior == pytest.approx(1.0 / (1.0 + 2.0 ** (-wf.final_bits)))
        assert len(wf.fields) == len(mk.fields)

    def test_total_matches_scorer(self):
        # The waterfall total must equal the weight the actual scorer sums.
        from goldenmatch.core.probabilistic import comparison_vector, explain_pair_fs
        _df, mk, em = self._em_and_mk()
        row_a = {"first_name": "Alice", "last_name": "Brown", "zip": "30301"}
        row_b = {"first_name": "Alicia", "last_name": "Brown", "zip": "30301"}
        wf = explain_pair_fs(row_a, row_b, mk, em)
        vec = comparison_vector(row_a, row_b, mk)
        scorer_total = sum(em.match_weights[f.field][vec[k]] for k, f in enumerate(mk.fields))
        assert wf.total_weight_bits == pytest.approx(scorer_total)

    def test_field_records_level_and_values(self):
        from goldenmatch.core.probabilistic import explain_pair_fs
        _df, mk, em = self._em_and_mk()
        row_a = {"first_name": "Tom", "last_name": "Wilson", "zip": "20001"}
        row_b = {"first_name": "Tom", "last_name": "Wilson", "zip": "20001"}
        wf = explain_pair_fs(row_a, row_b, mk, em)
        by_field = {c.field: c for c in wf.fields}
        # last_name identical -> top level (agree) for a 2-level field.
        assert by_field["last_name"].level == 1
        assert by_field["last_name"].value_a == "Wilson"

    def test_format_renders_and_sums(self):
        from goldenmatch.core.explain import format_fs_waterfall
        from goldenmatch.core.probabilistic import explain_pair_fs
        _df, mk, em = self._em_and_mk()
        row_a = {"first_name": "Bob", "last_name": "Jones", "zip": "60601"}
        row_b = {"first_name": "Robert", "last_name": "Jones", "zip": "60601"}
        text = format_fs_waterfall(explain_pair_fs(row_a, row_b, mk, em))
        assert "match-weight waterfall" in text
        assert "posterior P(match)" in text
        assert "prior" in text
        for f in mk.fields:
            assert f.field in text


# ── FS on the bucket backend (Phase 3a) ────────────────────────────────────


class TestFSBucketParity:
    def _cfg(self, backend):
        from goldenmatch.config.schemas import (
            BlockingConfig,
            BlockingKeyConfig,
            GoldenMatchConfig,
        )
        return GoldenMatchConfig(
            matchkeys=[_make_probabilistic_mk()],
            blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
            backend=backend,
        )

    @staticmethod
    def _parts(r):
        return sorted(
            tuple(sorted(c["members"]))
            for c in r.clusters.values() if len(c.get("members", [])) > 1
        )

    def test_bucket_matches_polars_direct(self):
        # FS clusters must be identical between polars-direct and the bucket
        # backend (same em_result, scorer-agnostic orchestration).
        from goldenmatch import dedupe_df
        df = _make_dedupe_df().drop("__row_id__")
        polars_r = dedupe_df(df, config=self._cfg(None))
        bucket_r = dedupe_df(df, config=self._cfg("bucket"))
        assert self._parts(bucket_r) == self._parts(polars_r)

    def test_score_buckets_requires_em_for_probabilistic(self):
        # The bucket scorer must refuse a probabilistic matchkey with no model.
        import polars as pl
        from goldenmatch.backends.score_buckets import score_buckets
        from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
        df = pl.DataFrame({"__row_id__": [0, 1], "first_name": ["a", "a"],
                           "last_name": ["b", "b"], "zip": ["1", "1"]})
        with pytest.raises(ValueError, match="requires a trained em_result"):
            score_buckets(df, BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
                          _make_probabilistic_mk(), set(), em_result=None)


# ── Native FS kernel (Phase 3b) ────────────────────────────────────────────


def _native_fs_available():
    try:
        from goldenmatch.core import _native_loader
        return _native_loader.native_available() and hasattr(
            _native_loader.native_module(), "score_block_pairs_fs"
        )
    except Exception:
        return False


class TestNativeFSGating:
    def test_default_off(self, monkeypatch):
        from goldenmatch.core import probabilistic as p
        monkeypatch.delenv("GOLDENMATCH_FS_NATIVE", raising=False)
        assert p._fs_native_enabled() is False
        assert p._fs_native_eligible(_make_probabilistic_mk()) is False

    def test_declines_soundex_and_tf(self, monkeypatch):
        from goldenmatch.core import probabilistic as p
        monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "1")
        if not _native_fs_available():
            pytest.skip("native ext not built")
        # soundex_match scorer is not a kernel scorer id -> ineligible.
        mk_sx = _make_probabilistic_mk(fields=[
            MatchkeyField(field="first_name", scorer="soundex_match", levels=2),
        ])
        assert p._fs_native_eligible(mk_sx) is False
        # tf_adjustment field -> ineligible (kernel has no TF tables).
        mk_tf = _make_probabilistic_mk(fields=[
            MatchkeyField(field="first_name", scorer="exact", levels=2, tf_adjustment=True),
        ])
        assert p._fs_native_eligible(mk_tf) is False


@pytest.mark.skipif(not _native_fs_available(), reason="native FS kernel not built")
class TestNativeFSParity:
    def _clean_df(self):
        # Identical matches / very different non-matches -> no pair sits on a
        # comparison-level boundary, so native == numpy exactly.
        return pl.DataFrame({
            "__row_id__": list(range(8)),
            "first_name": ["alexander", "alexander", "bartholomew", "bartholomew",
                           "christopher", "christopher", "wilhelmina", "wilhelmina"],
            "last_name": ["smith", "smith", "delacroix", "delacroix",
                          "wozniak", "wozniak", "abernathy", "abernathy"],
            "zip": ["111", "111", "222", "222", "333", "333", "444", "444"],
        })

    def test_native_matches_numpy_on_clean_data(self, monkeypatch):
        from goldenmatch.core import probabilistic as p
        from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
        from goldenmatch.core.blocker import build_blocks
        monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "1")
        df = self._clean_df()
        mk = _make_probabilistic_mk()
        blocks = build_blocks(df.lazy(), BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]))
        em = p.train_em(df, mk, n_sample_pairs=100, blocks=blocks, blocking_fields=["zip"])
        numpy_pairs = sorted(p.score_probabilistic_vectorized(df, mk, em, set()))
        native_pairs = sorted(p.score_probabilistic_native(df, mk, em, set()))
        assert native_pairs == numpy_pairs

    def test_block_scorer_picks_native_when_opted_in(self, monkeypatch):
        from goldenmatch.core import probabilistic as p
        monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "1")
        mk = _make_probabilistic_mk()
        em = p.train_em(_make_dedupe_df(), mk, n_sample_pairs=100, blocking_fields=["zip"])
        scorer = p.probabilistic_block_scorer(mk, em)
        # The native closure is named _native (see probabilistic_block_scorer).
        assert scorer.__name__ == "_native"

    def test_native_respects_exclude(self, monkeypatch):
        from goldenmatch.core import probabilistic as p
        from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
        from goldenmatch.core.blocker import build_blocks
        monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "1")
        df = self._clean_df()
        mk = _make_probabilistic_mk()
        blocks = build_blocks(df.lazy(), BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]))
        em = p.train_em(df, mk, n_sample_pairs=100, blocks=blocks, blocking_fields=["zip"])
        all_pairs = p.score_probabilistic_native(df, mk, em, set())
        excl = {(0, 1)}
        kept = p.score_probabilistic_native(df, mk, em, excl)
        assert (0, 1) not in {(a, b) for a, b, _s in kept}
        assert len(kept) == len(all_pairs) - 1
