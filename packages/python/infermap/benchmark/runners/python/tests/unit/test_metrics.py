"""Tests for the four metrics."""
from __future__ import annotations

from infermap_bench.metrics import (
    MetricInput,
    Prediction,
    expected_calibration_error,
    extract_predictions,
    f1_per_case,
    macro_mean,
    mean_reciprocal_rank,
    micro_f1,
    top1_accuracy,
)


def _make_input(
    source_fields: list[str],
    target_fields: list[str],
    expected_mappings: list[tuple[str, str]],
    expected_unmapped_src: list[str] | None = None,
    expected_unmapped_tgt: list[str] | None = None,
    actual_mappings: list[tuple[str, str, float]] | None = None,
    score_matrix: dict[str, dict[str, float]] | None = None,
) -> MetricInput:
    return MetricInput(
        source_fields=source_fields,
        target_fields=target_fields,
        expected_mappings=[{"source": s, "target": t} for s, t in expected_mappings],
        expected_unmapped_source=expected_unmapped_src or [],
        expected_unmapped_target=expected_unmapped_tgt or [],
        actual_mappings=[
            {"source": s, "target": t, "confidence": c}
            for s, t, c in (actual_mappings or [])
        ],
        score_matrix=score_matrix or {},
        min_confidence=0.3,
    )


class TestTop1:
    def test_perfect(self):
        inp = _make_input(
            source_fields=["a", "b"],
            target_fields=["A", "B"],
            expected_mappings=[("a", "A"), ("b", "B")],
            actual_mappings=[("a", "A", 0.9), ("b", "B", 0.9)],
        )
        assert top1_accuracy(inp) == 1.0

    def test_all_wrong(self):
        inp = _make_input(
            source_fields=["a", "b"],
            target_fields=["A", "B"],
            expected_mappings=[("a", "A"), ("b", "B")],
            actual_mappings=[("a", "B", 0.9), ("b", "A", 0.9)],
        )
        assert top1_accuracy(inp) == 0.0

    def test_correctly_unmapped_counts(self):
        inp = _make_input(
            source_fields=["a", "b"],
            target_fields=["A"],
            expected_mappings=[("a", "A")],
            expected_unmapped_src=["b"],
            actual_mappings=[("a", "A", 0.9)],  # b correctly unmapped
        )
        assert top1_accuracy(inp) == 1.0

    def test_incorrectly_mapped(self):
        inp = _make_input(
            source_fields=["a", "b"],
            target_fields=["A"],
            expected_mappings=[("a", "A")],
            expected_unmapped_src=["b"],
            actual_mappings=[("a", "A", 0.9), ("b", "A", 0.5)],
        )
        assert top1_accuracy(inp) == 0.5

    def test_empty_source_fields(self):
        inp = _make_input(
            source_fields=[], target_fields=[], expected_mappings=[]
        )
        assert top1_accuracy(inp) == 0.0

    def test_expected_mapped_but_not_predicted(self):
        """Source was expected to map to a target but isn't in actual_mappings
        at all — the predicted target is effectively None, so it mismatches
        the expected X. Score for this source should be 0."""
        inp = _make_input(
            source_fields=["a"],
            target_fields=["A"],
            expected_mappings=[("a", "A")],
            actual_mappings=[],  # nothing predicted — 'a' silently dropped
        )
        assert top1_accuracy(inp) == 0.0


class TestF1:
    def test_perfect(self):
        inp = _make_input(
            source_fields=["a", "b"], target_fields=["A", "B"],
            expected_mappings=[("a", "A"), ("b", "B")],
            actual_mappings=[("a", "A", 0.9), ("b", "B", 0.9)],
        )
        tp, fp, fn = f1_per_case(inp)
        assert (tp, fp, fn) == (2, 0, 0)
        assert micro_f1([(tp, fp, fn)]) == 1.0

    def test_zero(self):
        inp = _make_input(
            source_fields=["a"], target_fields=["A"],
            expected_mappings=[("a", "A")],
            actual_mappings=[],  # predicted nothing
        )
        tp, fp, fn = f1_per_case(inp)
        assert (tp, fp, fn) == (0, 0, 1)
        assert micro_f1([(tp, fp, fn)]) == 0.0

    def test_empty_expected_empty_predicted(self):
        inp = _make_input(
            source_fields=["a"], target_fields=["A"],
            expected_mappings=[],
            expected_unmapped_src=["a"], expected_unmapped_tgt=["A"],
            actual_mappings=[],
        )
        tp, fp, fn = f1_per_case(inp)
        assert (tp, fp, fn) == (0, 0, 0)
        # Perfect-negative case: nothing expected, nothing predicted → F1 = 1.0
        assert micro_f1([(tp, fp, fn)]) == 1.0

    def test_mixed(self):
        # 4 expected, predicted 3 correctly + 1 false positive
        inp = _make_input(
            source_fields=["a", "b", "c", "d"], target_fields=["A", "B", "C", "D", "X"],
            expected_mappings=[("a", "A"), ("b", "B"), ("c", "C"), ("d", "D")],
            actual_mappings=[("a", "A", 0.9), ("b", "B", 0.9), ("c", "C", 0.9), ("d", "X", 0.6)],
        )
        tp, fp, fn = f1_per_case(inp)
        assert (tp, fp, fn) == (3, 1, 1)
        # precision = 3/4, recall = 3/4, F1 = 0.75
        assert micro_f1([(tp, fp, fn)]) == 0.75

    def test_micro_f1_sums_across_cases(self):
        # Two cases: (2,0,0) and (1,1,1). Total: tp=3, fp=1, fn=1.
        # precision=3/4, recall=3/4, F1=0.75
        assert micro_f1([(2, 0, 0), (1, 1, 1)]) == 0.75

    def test_f1_deduplicates_predictions(self):
        """Set semantics: two identical predicted mappings count as one TP,
        not two. Prevents a buggy engine from gaming precision by duplicating."""
        inp = _make_input(
            source_fields=["a"], target_fields=["A"],
            expected_mappings=[("a", "A")],
            actual_mappings=[("a", "A", 0.9), ("a", "A", 0.8)],  # same pair twice
        )
        tp, fp, fn = f1_per_case(inp)
        assert (tp, fp, fn) == (1, 0, 0)


class TestMRR:
    def test_rank_1(self):
        inp = _make_input(
            source_fields=["a"], target_fields=["A", "B", "C"],
            expected_mappings=[("a", "A")],
            score_matrix={"a": {"A": 0.9, "B": 0.5, "C": 0.3}},
        )
        assert mean_reciprocal_rank(inp) == 1.0

    def test_rank_3(self):
        inp = _make_input(
            source_fields=["a"], target_fields=["A", "B", "C"],
            expected_mappings=[("a", "C")],
            score_matrix={"a": {"A": 0.9, "B": 0.5, "C": 0.3}},
        )
        assert abs(mean_reciprocal_rank(inp) - (1.0 / 3)) < 1e-9

    def test_missing_from_matrix(self):
        inp = _make_input(
            source_fields=["a"], target_fields=["B"],
            expected_mappings=[("a", "B")],
            score_matrix={"a": {}},
        )
        assert mean_reciprocal_rank(inp) == 0.0

    def test_source_absent_from_matrix(self):
        inp = _make_input(
            source_fields=["a"], target_fields=["B"],
            expected_mappings=[("a", "B")],
            score_matrix={},  # no row at all
        )
        assert mean_reciprocal_rank(inp) == 0.0

    def test_empty_expected(self):
        inp = _make_input(
            source_fields=["a"], target_fields=["A"],
            expected_mappings=[],
            expected_unmapped_src=["a"], expected_unmapped_tgt=["A"],
        )
        assert mean_reciprocal_rank(inp) == 1.0

    def test_deterministic_tie_break(self):
        inp = _make_input(
            source_fields=["a"], target_fields=["A", "B", "C"],
            expected_mappings=[("a", "B")],
            score_matrix={"a": {"A": 0.5, "B": 0.5, "C": 0.5}},
        )
        # All tied → sort by target name ascending → A, B, C → B at rank 2 → 0.5
        assert mean_reciprocal_rank(inp) == 0.5

    def test_averages_across_multiple_sources(self):
        inp = _make_input(
            source_fields=["a", "b"], target_fields=["A", "B"],
            expected_mappings=[("a", "A"), ("b", "B")],
            score_matrix={
                "a": {"A": 0.9, "B": 0.1},  # rank 1 → 1.0
                "b": {"A": 0.9, "B": 0.1},  # correct is B, scored second → rank 2 → 0.5
            },
        )
        assert mean_reciprocal_rank(inp) == 0.75


class TestECE:
    def test_perfect_calibration(self):
        # 90% confidence, 9/10 correct → ECE ~ 0.0
        preds = [Prediction(confidence=0.9, correct=True)] * 9 + [Prediction(confidence=0.9, correct=False)]
        ece = expected_calibration_error(preds, num_bins=10)
        assert ece < 0.01

    def test_overconfident(self):
        # 99% confidence, 50% correct → gap ~ 0.49
        preds = [Prediction(confidence=0.99, correct=True)] * 5 + [Prediction(confidence=0.99, correct=False)] * 5
        ece = expected_calibration_error(preds, num_bins=10)
        assert 0.45 < ece < 0.55

    def test_empty(self):
        assert expected_calibration_error([]) == 0.0

    def test_single_prediction(self):
        ece = expected_calibration_error([Prediction(confidence=0.8, correct=True)])
        # single bin, conf=0.8, acc=1.0, gap=0.2
        assert abs(ece - 0.2) < 1e-9

    def test_multi_bin_weighted(self):
        """ECE must correctly weight bins by population.

        Bin 1 (conf 0.1-0.2): 8 predictions, 4 correct → conf 0.15, acc 0.5, gap 0.35
        Bin 9 (conf 0.9-1.0): 2 predictions, 2 correct → conf 0.95, acc 1.0, gap 0.05
        Total 10. ECE = 0.8*0.35 + 0.2*0.05 = 0.28 + 0.01 = 0.29
        """
        preds = (
            [Prediction(confidence=0.15, correct=True)] * 4
            + [Prediction(confidence=0.15, correct=False)] * 4
            + [Prediction(confidence=0.95, correct=True)] * 2
        )
        ece = expected_calibration_error(preds, num_bins=10)
        assert abs(ece - 0.29) < 1e-9


class TestHelpers:
    def test_macro_mean_empty(self):
        assert macro_mean([]) == 0.0

    def test_macro_mean_average(self):
        assert macro_mean([0.5, 0.5, 1.0]) == 2.0 / 3.0

    def test_extract_predictions_maps_correctness(self):
        inp = _make_input(
            source_fields=["a", "b"], target_fields=["A", "B"],
            expected_mappings=[("a", "A"), ("b", "B")],
            actual_mappings=[("a", "A", 0.9), ("b", "X", 0.5)],  # b wrong
        )
        preds = extract_predictions(inp)
        assert len(preds) == 2
        assert preds[0].confidence == 0.9 and preds[0].correct is True
        assert preds[1].confidence == 0.5 and preds[1].correct is False
