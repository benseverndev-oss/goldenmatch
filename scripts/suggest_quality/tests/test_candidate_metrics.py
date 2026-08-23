"""Tests for the blocking-stage candidate metrics.

These are the metric the suggest scorecard was missing: every gated metric is
downstream of blocking, so a blocking regression only ever showed up as a
diffuse F1 wobble attributable to the scorer. See
docs/superpowers/specs/2026-08-21-candidate-recall-gate-design.md.
"""
import math

from scripts.suggest_quality.metrics import candidate_metrics


def test_single_block_holding_everything_is_perfect_recall_and_expensive():
    """The degenerate case, which is exactly why recall cannot gate alone.

    One block over 10 records reaches recall 1.0 -- and costs 45 comparisons.
    A recall floor without a cost ceiling would call this an improvement.
    """
    m = candidate_metrics([list(range(10))], {(0, 1), (2, 3), (8, 9)})
    assert m["candidate_recall"] == 1.0
    assert m["candidate_pairs"] == 45  # 10*9/2


def test_perfect_blocking_is_perfect_recall_and_cheap():
    """Same recall as above at a fraction of the cost -- the contrast that
    makes the pair of metrics informative rather than either one alone."""
    m = candidate_metrics([[0, 1], [2, 3], [8, 9]], {(0, 1), (2, 3), (8, 9)})
    assert m["candidate_recall"] == 1.0
    assert m["candidate_pairs"] == 3  # three 2-row blocks


def test_recall_drops_when_a_true_pair_is_split_across_blocks():
    """The direction the gate exists to catch: a pair blocking no longer emits."""
    m = candidate_metrics([[0, 1], [2], [3]], {(0, 1), (2, 3)})
    assert m["candidate_recall"] == 0.5
    assert m["candidate_pairs"] == 1


def test_pair_co_blocked_by_two_passes_counts_once_for_recall_twice_for_cost():
    """Multi-pass overlap: recall must not double-count, cost must.

    The pair (0,1) shares two blocks. It is one recovered pair, but the scorer
    really does compare it twice, so `candidate_pairs` counts both.
    """
    m = candidate_metrics([[0, 1], [0, 1]], {(0, 1)})
    assert m["candidate_recall"] == 1.0
    assert m["candidate_pairs"] == 2


def test_empty_ground_truth_is_not_applicable_not_zero():
    """Blocking-shape anchors carry no truth. Reporting 0.0 would look like a
    total regression and would gate against nothing; nan mirrors baseline_f1."""
    m = candidate_metrics([[0, 1, 2]], set())
    assert math.isnan(m["candidate_recall"])
    assert m["candidate_pairs"] == 3


def test_gt_row_absent_from_every_block_counts_as_a_miss():
    """A record blocking dropped entirely (null/sentinel key) is unreachable."""
    m = candidate_metrics([[0, 1]], {(0, 1), (0, 99)})
    assert m["candidate_recall"] == 0.5


def test_no_blocks_is_zero_recall_not_a_crash():
    m = candidate_metrics([], {(0, 1)})
    assert m["candidate_recall"] == 0.0
    assert m["candidate_pairs"] == 0


def test_singleton_blocks_cost_nothing():
    """n*(n-1)/2 == 0 for n<=1: blocks that cannot produce a pair are free."""
    m = candidate_metrics([[0], [1], [2]], {(0, 1)})
    assert m["candidate_recall"] == 0.0
    assert m["candidate_pairs"] == 0


def test_derived_column_blocking_is_recorded_as_an_error_not_a_silent_nan():
    """Pins the known v1 limitation so it cannot be rediscovered by surprise.

    The pipeline blocks on a PREPARED frame; this metric blocks on the input
    one. A config keyed on a column a later stage derives (dblp_acm's
    `__title_key__`, created by domain extraction) cannot be measured here.

    What this test locks is the FAILURE MODE, not the limitation: it must land
    on `candidate_error` with a legible reason, never as a bare nan that reads
    like "this dataset has no ground truth". If someone fixes the prep gap,
    this test should be replaced by a real measurement -- it is a tripwire, not
    an endorsement.
    """
    from scripts.suggest_quality.oracle import _record_candidate_metrics

    class _BlockingCfg:
        pass

    class _Cfg:
        blocking = _BlockingCfg()

    record: dict = {}
    # A frame lacking the derived column the config keys on. build_blocks
    # raises; the helper must convert that into a recorded, readable error.
    _record_candidate_metrics(record, object(), _Cfg(), {(0, 1)})

    assert record.get("candidate_error"), (
        "a failure to compute must be RECORDED -- an un-computed metric that "
        "looks inapplicable is the check-does-not-fire class this exists to expose"
    )
    assert "candidate_recall" not in record or record["candidate_recall"] != 0.0, (
        "must not report 0.0 recall on failure -- that reads as a total "
        "blocking regression rather than 'not measured'"
    )
