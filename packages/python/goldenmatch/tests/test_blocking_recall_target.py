"""#2717: the matchkey recall denominator must be reachable, and ranking must
not trade recall away silently.

Two coupled fixes, and the coupling is the point -- either alone is wrong:

1. `_target_pairs_from_matchkey` built row ids with `sample_frame.native
   .with_columns(...)`, a POLARS call. On the arrow-native lane `native` is a
   `pa.Table`, so it raised, `_build_recall_target` caught it, and every recall
   estimate silently came from `_target_pairs_from_similarity` -- the proxy its
   own docstring calls WEAK (Amazon-Google: 2,355 pairs, 35 true, 1.5%).

2. Ranking is `score * estimated_recall`, so a cheap plan outranks a much
   better one. `_apply_recall_tradeoff_gate` refuses a plan retaining less than
   `_RECALL_TRADEOFF_RATIO` of the best available.

Measured on real DBLP-ACM against `DBLP-ACM_perfectMapping.csv`:

    denominator    candidate      estimate   TRUE recall
    weak proxy     venue[:3]        0.2032        0.0594
    weak proxy     title[:5]        0.0407        0.9820   <- INVERTED
    matchkey       venue[:3]        0.3913        0.0594
    matchkey       title[:5]        0.9565        0.9820   <- tracks truth

Applying the gate over the WEAK denominator promotes `venue[:3]` and takes
DBLP-ACM from 98.2% recall to 5.9% at 61x the comparisons. That was built,
measured, and reverted -- hence `test_gate_is_inert_without_a_usable_estimate`.
"""
from __future__ import annotations

import pytest

pa = pytest.importorskip("pyarrow")

from goldenmatch.core import block_analyzer as ba  # noqa: E402


class _S:
    """Minimal stand-in for a BlockingSuggestion (only the gate's inputs)."""

    def __init__(self, description, estimated_recall, score=1.0):
        self.description = description
        self.estimated_recall = estimated_recall
        self.score = score


def test_the_matchkey_denominator_is_reachable_on_an_arrow_table():
    """The regression: this raised AttributeError on a pa.Table and fell back."""
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
    from goldenmatch.core.frame import to_frame

    t = pa.table({
        "id": [f"r{i}" for i in range(40)],
        "title": [f"a study of topic {i % 8}" for i in range(40)],
    })
    mk = MatchkeyConfig(name="m", type="weighted", threshold=0.5,
                        fields=[MatchkeyField(field="title", scorer="token_sort", weight=1.0)])
    pairs = ba._target_pairs_from_matchkey(to_frame(t), mk)
    assert isinstance(pairs, set)
    assert pairs, "the matchkey emitted no pairs on obviously-duplicate titles"


def test_gate_promotes_a_higher_recall_plan_over_a_cheaper_one():
    out = ba._apply_recall_tradeoff_gate([
        _S("cheap", 0.30, score=10.0),      # would win on score*recall
        _S("thorough", 0.95, score=1.0),
    ])
    assert out[0].description == "thorough", [s.description for s in out]


def test_a_plan_within_the_ratio_keeps_its_place():
    """Cost still decides among plans that clear the bar."""
    out = ba._apply_recall_tradeoff_gate([
        _S("cheap-enough", 0.80, score=10.0),
        _S("thorough", 0.95, score=1.0),
    ])
    assert out[0].description == "cheap-enough", [s.description for s in out]


def test_gate_is_inert_without_a_usable_estimate():
    """best_recall == 0 means recall could not be estimated at all.

    Gating on that would order the list by a number that means nothing. It
    returns unchanged instead -- a gate that starves the caller of candidates
    is worse than the ranking it fixes.
    """
    same = [_S("a", 0.0, score=5.0), _S("b", 0.0, score=1.0)]
    assert [s.description for s in ba._apply_recall_tradeoff_gate(same)] == ["a", "b"]


def test_gate_never_empties_the_list():
    out = ba._apply_recall_tradeoff_gate([_S("only", 0.42, score=1.0)])
    assert len(out) == 1


def test_gated_candidates_are_retained_not_discarded():
    """Demoted, not deleted -- callers below rank 0 still see the full list."""
    out = ba._apply_recall_tradeoff_gate([
        _S("cheap", 0.30, score=10.0),
        _S("thorough", 0.95, score=1.0),
    ])
    assert len(out) == 2, [s.description for s in out]
    assert out[-1].description == "cheap"
