"""#2717: the matchkey recall denominator must be reachable on BOTH lanes.

`_target_pairs_from_matchkey` built row ids with
`sample_frame.native.with_columns(pl.Series(...))` -- a polars call. On the
arrow-native lane `native` is a `pa.Table`, so it raised,
`_build_recall_target` caught it, and every recall estimate silently came from
`_target_pairs_from_similarity` -- the proxy its own docstring calls WEAK
(Amazon-Google: 2,355 sample pairs, 35 true, 1.5% precision).

That proxy is ANTI-correlated with truth, not merely noisy. Measured on real
DBLP-ACM against `DBLP-ACM_perfectMapping.csv`:

    denominator   candidate     estimate   TRUE recall
    weak proxy    venue[:3]       0.2032        0.0594
    weak proxy    title[:5]       0.0407        0.9820   <- INVERTED
    matchkey      venue[:3]       0.3913        0.0594
    matchkey      title[:5]       0.9565        0.9820   <- tracks truth

`find_fuzzy_matches` accepted a `pa.Table` all along -- its body reads through
the `_to_frame_d5` seam and branches on `to_dicts`/`to_pylist`. Only the
annotation said polars, and that alone was enough to make this caller convert.
"""
from __future__ import annotations

import pytest

pa = pytest.importorskip("pyarrow")
pl = pytest.importorskip("polars")

from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField  # noqa: E402
from goldenmatch.core import block_analyzer as ba  # noqa: E402
from goldenmatch.core.frame import to_frame  # noqa: E402
from goldenmatch.core.scorer import find_fuzzy_matches  # noqa: E402


def _mk():
    return MatchkeyConfig(name="m", type="weighted", threshold=0.5,
                          fields=[MatchkeyField(field="title", scorer="token_sort", weight=1.0)])


def _data():
    return {
        "__row_id__": list(range(6)),
        "title": ["quick brown fox", "quick brown fox!", "totally other",
                  "totally other thing", "zzz", "qqq"],
    }


class _S:
    def __init__(self, description, estimated_recall, score=1.0):
        self.description = description
        self.estimated_recall = estimated_recall
        self.score = score


def test_find_fuzzy_matches_is_lane_agnostic():
    """The premise of the fix: arrow and polars must agree exactly."""
    d = _data()
    p = find_fuzzy_matches(pl.DataFrame(d), _mk())
    a = find_fuzzy_matches(pa.table(d), _mk())
    assert {(i, j) for i, j, _ in p} == {(i, j) for i, j, _ in a}
    assert max(abs(x[2] - y[2]) for x, y in zip(sorted(p), sorted(a))) == 0.0


def test_the_recall_target_works_on_an_arrow_table():
    """The regression itself: this raised AttributeError and fell back."""
    t = pa.table({
        "id": [f"r{i}" for i in range(30)],
        "title": [f"a study of topic {i % 6}" for i in range(30)],
    })
    pairs = ba._target_pairs_from_matchkey(to_frame(t), _mk())
    assert pairs, "no pairs from obviously-duplicate titles -- the fallback is back"


def test_row_ids_stay_positional_even_if_input_carries_its_own():
    """Callers index parallel per-row lists with these ids.

    `ensure_row_ids` REUSES an existing `__row_id__` (#844), so an upstream
    frame carrying its own numbering would silently desynchronise `_retention`.
    The implementation drops it first; this pins that.
    """
    t = pa.table({
        "__row_id__": [900 + i for i in range(20)],   # NOT positional
        "title": [f"a study of topic {i % 4}" for i in range(20)],
    })
    pairs = ba._target_pairs_from_matchkey(to_frame(t), _mk())
    assert pairs, "expected pairs"
    flat = {i for pair in pairs for i in pair}
    assert max(flat) < 20, f"ids are not positional into the frame: {sorted(flat)[-3:]}"


def test_gate_promotes_a_higher_recall_plan_over_a_cheaper_one():
    out = ba._apply_recall_tradeoff_gate([
        _S("cheap", 0.30, score=10.0), _S("thorough", 0.95, score=1.0)])
    assert out[0].description == "thorough"


def test_a_plan_within_the_ratio_keeps_its_place():
    """Cost still decides among plans that clear the bar."""
    out = ba._apply_recall_tradeoff_gate([
        _S("cheap-enough", 0.80, score=10.0), _S("thorough", 0.95, score=1.0)])
    assert out[0].description == "cheap-enough"


def test_gate_is_inert_without_a_usable_estimate():
    same = [_S("a", 0.0, score=5.0), _S("b", 0.0, score=1.0)]
    assert [s.description for s in ba._apply_recall_tradeoff_gate(same)] == ["a", "b"]


def test_gated_candidates_are_demoted_not_dropped():
    out = ba._apply_recall_tradeoff_gate([
        _S("cheap", 0.30, score=10.0), _S("thorough", 0.95, score=1.0)])
    assert len(out) == 2 and out[-1].description == "cheap"
