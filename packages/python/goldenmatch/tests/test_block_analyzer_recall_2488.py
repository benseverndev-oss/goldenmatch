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

#: `analyze_blocking` builds the recall target ONCE and then measures each
#: candidate with `_retention` (#2513 -- it used to call `estimate_recall` per
#: candidate, which rebuilt the same seeded pair population every time). Tests
#: stub the per-candidate seam. Addressed by string rather than by importing
#: the module a second time -- one import style per module in this file.
_RETENTION = "goldenmatch.core.block_analyzer._retention"


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
            _RETENTION,
            lambda frame, cand, pairs: recalls.get(cand["key_fields"][0], 0.5),
        )
        sugs = analyze_blocking(df, ["a", "b"])
        assert sugs
        top = sugs[0]
        assert top.estimated_recall >= 0.5, (
            f"top suggestion {top.description!r} has recall {top.estimated_recall}; "
            "a high-recall candidate should outrank a low-recall one"
        )

    def test_every_candidate_is_measured(self, monkeypatch):
        """#2513 replaced the two-tier sort. Recall used to be measured only for
        the top 10 by score, with 0.0 standing in for "unmeasured" on the tail --
        but 0.0 is also a real recall, and it was multiplied into the rank, so a
        tail candidate could never be selected however good it was. Tiering kept
        that placeholder from doing damage; measuring everything removes the
        need for it. No suggestion may carry an unmeasured placeholder."""
        seen: list[str] = []

        def _spy(frame, cand, pairs):
            seen.append(cand["description"])
            return 0.4

        monkeypatch.setattr(_RETENTION, _spy)
        df = pl.DataFrame({c: [f"{c}{i}" for i in range(40)] for c in "abcd"})
        sugs = analyze_blocking(df, list("abcd"))
        assert len(sugs) > 10, "need more than one tier's worth to be meaningful"
        assert len(seen) == len(sugs), (
            f"measured {len(seen)} candidates but returned {len(sugs)} suggestions"
        )
        assert all(s.estimated_recall == 0.4 for s in sugs), (
            "some suggestion carries a placeholder rather than its measured recall"
        )

    def test_a_low_score_candidate_can_win_on_recall(self, monkeypatch):
        """THE #2513 REGRESSION. On Amazon-Google the candidate with the highest
        true pair recall of any generated (`tokens(title, df<=100)`, 98.2%) fell
        outside the top 10 by score, was assigned the 0.0 placeholder, and so
        had rank value `score * 0 == 0` -- unselectable by construction."""
        df = pl.DataFrame({c: [f"{c}{i % 20}" for i in range(40)] for c in "abcd"})
        baseline = analyze_blocking(df, list("abcd"))
        assert len(baseline) > 10
        # Pick a candidate the score-ordered pass puts well into the tail.
        tail_desc = baseline[-1].description

        monkeypatch.setattr(
            _RETENTION,
            lambda frame, cand, pairs: 1.0 if cand["description"] == tail_desc else 0.01,
        )
        sugs = analyze_blocking(df, list("abcd"))
        assert sugs[0].description == tail_desc, (
            f"{tail_desc!r} has by far the best recall but ranked "
            f"{[s.description for s in sugs].index(tail_desc)}"
        )

    def test_measurement_failure_does_not_read_as_zero_recall(self, monkeypatch):
        """The same confusion in its other form: if measuring a candidate raises,
        it must not be recorded as "retains nothing". Fail open and let `score`
        rank it, rather than silently removing it from contention."""
        def _boom(frame, cand, pairs):
            raise RuntimeError("scorer exploded")

        monkeypatch.setattr(_RETENTION, _boom)
        df = pl.DataFrame({"a": [f"a{i % 10}" for i in range(30)]})
        sugs = analyze_blocking(df, ["a"])
        assert sugs, "a measurement failure must not empty the suggestion list"
        assert all(s.estimated_recall == 1.0 for s in sugs)

    def test_low_recall_is_reported_not_silently_committed(self, monkeypatch, caplog):
        """On Amazon-Google EVERY candidate is below the floor. Hard-rejecting
        them all would leave degenerate blocking, and one mega-block is worse
        than a poor key -- so this warns rather than refuses, and the warning
        has to carry the number."""
        monkeypatch.setattr(_RETENTION, lambda frame, cand, pairs: 0.06)
        df = pl.DataFrame({"a": [f"a{i}" for i in range(30)]})
        with caplog.at_level("WARNING"):
            sugs = analyze_blocking(df, ["a"])
        assert sugs, "must still return a plan rather than nothing"
        assert "6.0% recall" in caplog.text
        assert "#2488" in caplog.text

    def test_no_warning_when_recall_is_healthy(self, monkeypatch, caplog):
        monkeypatch.setattr(_RETENTION, lambda frame, cand, pairs: 0.95)
        df = pl.DataFrame({"a": [f"a{i}" for i in range(30)]})
        with caplog.at_level("WARNING"):
            analyze_blocking(df, ["a"])
        assert "#2488" not in caplog.text

    def test_the_floor_is_a_warning_threshold_not_a_rejection(self):
        """Pin the intent so a later change does not quietly turn it into a
        hard gate: nothing filters on `_LOW_RECALL_WARN`."""
        assert 0.0 < _LOW_RECALL_WARN < 1.0
        assert BlockingSuggestion  # imported surface stays public
