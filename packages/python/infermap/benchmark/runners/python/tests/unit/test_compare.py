"""Tests for the report comparison (delta) logic."""
from __future__ import annotations

from infermap_bench.compare import Delta, compute_delta


def _metric_set(f1=0.5, top1=0.5, mrr=0.5, ece=0.1, n=10):
    return {"f1": f1, "top1": top1, "mrr": mrr, "ece": ece, "n": n}


def _report(overall_f1=0.5, per_case=None, by_difficulty=None, by_category=None, by_tag=None):
    return {
        "version": 1,
        "language": "python",
        "infermap_version": "0.1.0",
        "runner_version": "0.1.0",
        "ran_at": "2026-04-08T00:00:00Z",
        "duration_seconds": 1.0,
        "scorecard": {
            "overall": _metric_set(f1=overall_f1),
            "by_difficulty": by_difficulty or {},
            "by_category": by_category or {},
            "by_tag": by_tag or {},
        },
        "per_case": per_case or [],
        "failed_cases": [],
    }


def _per_case(id_: str, f1: float) -> dict:
    return {
        "id": id_, "f1": f1, "top1": f1, "mrr": f1,
        "expected_n": 2, "predicted_n": 2,
        "true_positives": 1, "false_positives": 1, "false_negatives": 1,
        "failed": False,
    }


class TestOverallDelta:
    def test_improvement_detected(self):
        baseline = _report(overall_f1=0.50)
        current = _report(overall_f1=0.60)
        delta = compute_delta(baseline, current)
        assert isinstance(delta, Delta)
        assert abs(delta.overall["f1"] - 0.10) < 1e-9
        assert delta.is_regression(threshold=0.02) is False

    def test_regression_detected(self):
        baseline = _report(overall_f1=0.50)
        current = _report(overall_f1=0.40)
        delta = compute_delta(baseline, current)
        assert abs(delta.overall["f1"] - (-0.10)) < 1e-9
        assert delta.is_regression(threshold=0.02) is True

    def test_regression_below_threshold(self):
        baseline = _report(overall_f1=0.50)
        current = _report(overall_f1=0.495)
        delta = compute_delta(baseline, current)
        assert delta.is_regression(threshold=0.02) is False

    def test_regression_exactly_at_threshold_not_regression(self):
        baseline = _report(overall_f1=0.50)
        current = _report(overall_f1=0.48)
        delta = compute_delta(baseline, current)
        assert delta.is_regression(threshold=0.02) is False

    def test_custom_threshold(self):
        baseline = _report(overall_f1=0.50)
        current = _report(overall_f1=0.45)
        delta = compute_delta(baseline, current)
        assert delta.is_regression(threshold=0.02) is True
        assert delta.is_regression(threshold=0.10) is False


class TestSliceDeltas:
    def test_by_difficulty(self):
        baseline = _report(
            by_difficulty={"easy": _metric_set(f1=0.9), "hard": _metric_set(f1=0.3)}
        )
        current = _report(
            by_difficulty={"easy": _metric_set(f1=0.95), "hard": _metric_set(f1=0.35)}
        )
        delta = compute_delta(baseline, current)
        assert abs(delta.by_difficulty["easy"]["f1"] - 0.05) < 1e-9
        assert abs(delta.by_difficulty["hard"]["f1"] - 0.05) < 1e-9

    def test_missing_slice_in_current_treated_as_zero(self):
        baseline = _report(
            by_difficulty={"easy": _metric_set(f1=0.9)}
        )
        current = _report(by_difficulty={})
        delta = compute_delta(baseline, current)
        assert abs(delta.by_difficulty["easy"]["f1"] - (-0.9)) < 1e-9

    def test_new_slice_in_current(self):
        baseline = _report(by_tag={})
        current = _report(by_tag={"new_tag": _metric_set(f1=0.7)})
        delta = compute_delta(baseline, current)
        assert abs(delta.by_tag["new_tag"]["f1"] - 0.7) < 1e-9


class TestPerCaseMovers:
    def test_detects_regression_case(self):
        baseline = _report(per_case=[_per_case("a", 0.9)])
        current = _report(per_case=[_per_case("a", 0.4)])
        delta = compute_delta(baseline, current)
        regressions, improvements = delta.top_movers(n=10, threshold=0.05)
        assert len(regressions) == 1
        assert regressions[0][0] == "a"
        assert regressions[0][1] == 0.9
        assert regressions[0][2] == 0.4
        assert abs(regressions[0][3] - (-0.5)) < 1e-9
        assert len(improvements) == 0

    def test_detects_improvement_case(self):
        baseline = _report(per_case=[_per_case("a", 0.2)])
        current = _report(per_case=[_per_case("a", 0.8)])
        delta = compute_delta(baseline, current)
        regressions, improvements = delta.top_movers(n=10, threshold=0.05)
        assert len(regressions) == 0
        assert len(improvements) == 1
        assert improvements[0][0] == "a"

    def test_threshold_filters_small_movements(self):
        baseline = _report(per_case=[_per_case("a", 0.5)])
        current = _report(per_case=[_per_case("a", 0.52)])
        delta = compute_delta(baseline, current)
        regressions, improvements = delta.top_movers(n=10, threshold=0.05)
        assert regressions == []
        assert improvements == []

    def test_top_n_limits(self):
        baseline_cases = [_per_case(f"c{i}", 0.9) for i in range(15)]
        current_cases = [_per_case(f"c{i}", 0.1) for i in range(15)]
        baseline = _report(per_case=baseline_cases)
        current = _report(per_case=current_cases)
        delta = compute_delta(baseline, current)
        regressions, _ = delta.top_movers(n=5, threshold=0.05)
        assert len(regressions) == 5

    def test_per_case_only_includes_intersection(self):
        baseline = _report(per_case=[_per_case("a", 0.5), _per_case("b", 0.5)])
        current = _report(per_case=[_per_case("a", 0.8)])
        delta = compute_delta(baseline, current)
        case_ids = {cid for cid, _, _ in delta.per_case_deltas}
        assert case_ids == {"a"}

    def test_sort_order_regressions_worst_first(self):
        baseline = _report(per_case=[_per_case("big", 0.9), _per_case("small", 0.9)])
        current = _report(per_case=[_per_case("big", 0.1), _per_case("small", 0.6)])
        delta = compute_delta(baseline, current)
        regressions, _ = delta.top_movers(n=10, threshold=0.05)
        assert [r[0] for r in regressions] == ["big", "small"]

    def test_sort_order_improvements_best_first(self):
        baseline = _report(per_case=[_per_case("big", 0.1), _per_case("small", 0.5)])
        current = _report(per_case=[_per_case("big", 0.9), _per_case("small", 0.7)])
        delta = compute_delta(baseline, current)
        _, improvements = delta.top_movers(n=10, threshold=0.05)
        assert [i[0] for i in improvements] == ["big", "small"]
