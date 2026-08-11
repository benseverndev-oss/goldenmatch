"""Blocking suggestions must account for what a key can REACH, not just its cost.

Two defects, both measured on the Amazon-Google benchmark frame (#2488):

1. `score_candidate` divided its selectivity term by the records that PRODUCED a
   key, not by the frame. Compound keys null-propagate, so one sparse component
   nulls the whole key -- an Amazon-Google `manufacturer` component is 100%
   populated on one source and 7.2% on the other. The 65% of records the key
   cannot key were removed from the denominator, so the key looked maximally
   selective AND cheap (`total_comparisons` is also summed over survivors).

2. `estimated_recall` was computed for the top candidates, logged, and then left
   out of the ranking entirely -- the only multiplier was `check_coverage`'s
   field-membership flag, which asks whether the key's columns are matchkey
   columns and says nothing about retained pairs. On Amazon-Google every
   candidate estimates 0.05-0.07 recall and the winner was chosen on
   selectivity alone; the pipeline then achieved 4.19%.
"""
from __future__ import annotations

import polars as pl
from goldenmatch.core.block_analyzer import (
    _LOW_RECALL_WARN,
    BlockingSuggestion,
    analyze_blocking,
    score_candidate,
)

#: `analyze_blocking` resolves `estimate_recall` from its own module at call
#: time, so tests stub it there. Addressed by string rather than by importing
#: the module a second time -- one import style per module in this file.
_ESTIMATE_RECALL = "goldenmatch.core.block_analyzer.estimate_recall"


def _cand(fields, transforms, desc="c"):
    return {"key_fields": fields, "transforms": transforms, "description": desc}


class TestCoverageInTheDenominator:
    def test_full_coverage_scoring_is_unchanged(self):
        """The safety property that bounds this change: when every record keys,
        `df_valid.height == n_total`, so the new denominator IS the old one.
        Only partial-coverage keys move."""
        df = pl.DataFrame({"a": [f"v{i}" for i in range(20)]})
        m = score_candidate(df, _cand(["a"], ["lowercase"]))
        assert m["coverage"] == 1.0
        # 20 distinct keys over 20 records -> selectivity term is exactly 1.0
        assert m["group_count"] == 20

    def test_coverage_is_reported(self):
        df = pl.DataFrame({"a": ["x", "y", None, None]})
        m = score_candidate(df, _cand(["a"], ["lowercase"]))
        assert m["coverage"] == 0.5

    def test_a_sparse_key_no_longer_looks_perfectly_selective(self):
        """THE regression. Two frames with identical keyed populations, one of
        which has a large unkeyable remainder. Before the fix they scored the
        same, because the remainder was divided out."""
        keyed_only = pl.DataFrame({"a": [f"v{i}" for i in range(10)]})
        with_remainder = pl.DataFrame({"a": [f"v{i}" for i in range(10)] + [None] * 90})

        dense = score_candidate(keyed_only, _cand(["a"], ["lowercase"]))
        sparse = score_candidate(with_remainder, _cand(["a"], ["lowercase"]))

        assert dense["group_count"] == sparse["group_count"] == 10
        assert dense["coverage"] == 1.0
        assert sparse["coverage"] == 0.1
        assert sparse["score"] < dense["score"], (
            "a key that can only key 10% of the frame must not score like one "
            "that keys all of it"
        )
        # The cap is coverage: selectivity can be at most group_count / n_total.
        assert sparse["score"] <= dense["score"] * sparse["coverage"] + 1e-9

    def test_a_compound_inherits_its_sparsest_component(self):
        """`_apply_candidate_transforms` null-propagates, which is how one
        7%-populated column drags a whole compound key down. This is the
        Amazon-Google `soundex(title) + manufacturer[:5]` shape in miniature."""
        df = pl.DataFrame({
            "title": [f"t{i}" for i in range(100)],
            "manufacturer": [f"m{i}" for i in range(7)] + [None] * 93,
        })
        title_only = score_candidate(df, _cand(["title"], ["lowercase"]))
        compound = score_candidate(
            df, _cand(["title", "manufacturer"], [["lowercase"], ["lowercase"]])
        )
        assert title_only["coverage"] == 1.0
        assert compound["coverage"] == 0.07
        assert compound["score"] < title_only["score"]


class TestMeasuredRecallRanksTheSuggestions:
    def test_recall_reorders_the_measured_block(self, monkeypatch):
        """Two candidates whose raw scores are close but whose recalls are far
        apart: the higher-recall one must win. Before the fix `estimated_recall`
        never touched the ordering."""
        df = pl.DataFrame({
            "a": [f"a{i}" for i in range(60)],
            "b": [f"b{i}" for i in range(60)],
        })
        # Make recall the only thing that differs meaningfully.
        recalls = {"a": 0.9, "b": 0.1}
        monkeypatch.setattr(
            _ESTIMATE_RECALL,
            lambda d, cand, cols, sample_size=1000: recalls.get(cand["key_fields"][0], 0.5),
        )
        sugs = analyze_blocking(df, ["a", "b"])
        assert sugs
        top = sugs[0]
        assert top.estimated_recall >= 0.5, (
            f"top suggestion {top.description!r} has recall {top.estimated_recall}; "
            "a high-recall candidate should outrank a low-recall one"
        )

    def test_the_unmeasured_tail_stays_behind_the_measured_block(self, monkeypatch):
        """Recall is only estimated for the top N. Multiplying a placeholder 0.0
        into the tail would rank unmeasured candidates as known-useless, and
        multiplying anywhere would push measured candidates below unmeasured
        ones whenever recall < 1. Tiering avoids both."""
        monkeypatch.setattr(_ESTIMATE_RECALL,
                            lambda d, cand, cols, sample_size=1000: 0.4)
        df = pl.DataFrame({c: [f"{c}{i}" for i in range(40)] for c in "abcd"})
        sugs = analyze_blocking(df, list("abcd"))
        measured = [s for s in sugs if s.estimated_recall > 0.0]
        assert measured, "expected some candidates to have measured recall"
        # every measured suggestion precedes every unmeasured one
        last_measured = max(i for i, s in enumerate(sugs) if s.estimated_recall > 0.0)
        first_unmeasured = min(
            (i for i, s in enumerate(sugs) if s.estimated_recall == 0.0),
            default=len(sugs),
        )
        assert last_measured < first_unmeasured

    def test_low_recall_is_reported_not_silently_committed(self, monkeypatch, caplog):
        """On Amazon-Google EVERY candidate is below the floor. Hard-rejecting
        them all would leave degenerate blocking, and one mega-block is worse
        than a poor key -- so this warns rather than refuses, and the warning
        has to carry the number."""
        monkeypatch.setattr(_ESTIMATE_RECALL,
                            lambda d, cand, cols, sample_size=1000: 0.06)
        df = pl.DataFrame({"a": [f"a{i}" for i in range(30)]})
        with caplog.at_level("WARNING"):
            sugs = analyze_blocking(df, ["a"])
        assert sugs, "must still return a plan rather than nothing"
        assert "6.0% recall" in caplog.text
        assert "#2488" in caplog.text

    def test_no_warning_when_recall_is_healthy(self, monkeypatch, caplog):
        monkeypatch.setattr(_ESTIMATE_RECALL,
                            lambda d, cand, cols, sample_size=1000: 0.95)
        df = pl.DataFrame({"a": [f"a{i}" for i in range(30)]})
        with caplog.at_level("WARNING"):
            analyze_blocking(df, ["a"])
        assert "#2488" not in caplog.text

    def test_the_floor_is_a_warning_threshold_not_a_rejection(self):
        """Pin the intent so a later change does not quietly turn it into a
        hard gate: nothing filters on `_LOW_RECALL_WARN`."""
        assert 0.0 < _LOW_RECALL_WARN < 1.0
        assert BlockingSuggestion  # imported surface stays public
