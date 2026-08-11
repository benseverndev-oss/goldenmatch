"""Learned blocking must gate rules on ABSOLUTE full-frame cost, not a ratio.

`min_reduction` is a floor on ``1 - blocked_pairs / total_pairs``. Both terms
grow as n^2, so the ratio is SCALE-INVARIANT and cannot express what a rule
costs. Measured on the QIS realistic shape, sample vs full frame:

    predicate            sample red   full red      full pairs @ 2M
    email:exact             1.0000     1.0000              3,281,650
    first_name:first_3      0.9984     0.9984          3,120,436,260
    birth_year:exact        0.9846     0.9846         30,773,502,725
    id:first_3              0.9667     0.9667         66,665,444,450

Identical to four decimals at both scales, and every one clears the default
``min_reduction=0.90`` -- including the 66-billion-pair one. That is #2474: the
selector had no quantity that could tell these apart.

The tests below pin the projection, the gate, and the two paths the gate must
not break: no-`total_rows` callers keep their exact previous behaviour, and a
budget wipeout falls through to the depth-2 search rather than to nothing.
"""
from __future__ import annotations

import json
import math

import polars as pl
import pytest
from goldenmatch.core.block_projection import project_block_counts
from goldenmatch.core.learned_blocking import (
    BlockingPredicate,
    BlockingRule,
    _reject_exploding_rules,
    learn_blocking_rules,
    load_learned_rules,
    save_learned_rules,
)


class TestProjectBlockCounts:
    def test_no_projection_when_sample_is_the_whole_frame(self):
        assert project_block_counts([10, 4, 1], 15, 15) == (10, 45 + 6)

    def test_empty_input_is_zero_cost_not_an_error(self):
        assert project_block_counts([], 100, 1000) == (0, 0)

    def test_saturated_key_grows_by_the_full_row_ratio(self):
        """Few distinct values (d -> 0): a bigger frame just grows each block."""
        # 2 blocks in a 1000-row sample, projected to 10x the rows.
        max_block, pairs = project_block_counts([500, 500], 1_000, 10_000)
        # d = 2/1000 = 0.002, growth = 1 + 9*0.998 = 9.982
        assert max_block == math.ceil(500 * 9.982)
        assert pairs == 2 * (max_block * (max_block - 1) // 2)

    def test_near_unique_key_barely_grows(self):
        """d -> 1: new values keep appearing, so blocks stay ~constant size.

        Growing them by the full ratio is what invented ~2.2B phantom pairs at
        30M and collapsed blocking to a single pass.
        """
        sizes = [1] * 990 + [2] * 5
        saturated_growth = 10.0
        max_block, _ = project_block_counts(sizes, 1_000, 10_000)
        assert max_block < 2 * saturated_growth

    def test_under_projection_is_the_safe_direction(self):
        """A near-unique key is under-projected; a coarse one is not.

        A cost gate can afford to let a cheap key through. It cannot afford to
        let a coarse one through, which is why the coarse case is the accurate
        one.
        """
        coarse = project_block_counts([500, 500], 1_000, 10_000)[1]
        near_unique = project_block_counts([1] * 1_000, 1_000, 10_000)[1]
        assert coarse > near_unique * 1_000

    def test_matches_the_autoconfig_formula_it_was_extracted_from(self):
        """Differential pin against the inlined implementation in #2474's parent.

        `_projected_pass_cost` used this arithmetic verbatim; the extraction must
        not have changed a single projected pair, or auto-config's static pass
        gate silently moves.
        """
        counts = {"a": 40, "b": 12, "c": 3, "d": 1}
        sample_n, full_n = 500, 250_000

        ratio = full_n / sample_n
        d = len(counts) / sample_n
        growth = 1.0 + (ratio - 1.0) * (1.0 - d)
        want_max, want_pairs = 0, 0
        for cnt in counts.values():
            b = math.ceil(cnt * growth)
            want_max = max(want_max, b)
            want_pairs += b * (b - 1) // 2

        assert project_block_counts(counts.values(), sample_n, full_n) == (
            want_max, want_pairs,
        )


def _rule(field: str, *, pairs: int) -> BlockingRule:
    return BlockingRule(
        predicates=[BlockingPredicate(field=field, transform="exact")],
        recall=1.0,
        reduction_ratio=0.99,
        projected_pairs=pairs,
    )


class TestRejectExplodingRules:
    def test_keeps_under_budget_drops_over(self):
        cheap, costly = _rule("a", pairs=1_000), _rule("b", pairs=10**11)
        assert _reject_exploding_rules([cheap, costly], 300_000_000, 5_000_000, "single") == [cheap]

    def test_the_budget_is_inclusive(self):
        exact = _rule("a", pairs=300_000_000)
        assert _reject_exploding_rules([exact], 300_000_000, 5_000_000, "single") == [exact]

    def test_every_rejection_is_logged_with_the_number_behind_it(self, caplog):
        """A blocking rule dropped without a trace is the #1837 failure mode:
        recall-only loss, invisible in precision. This function drops rules, so
        silence is not an option for it."""
        with caplog.at_level("WARNING"):
            _reject_exploding_rules([_rule("boom", pairs=66_665_444_450)],
                                    300_000_000, 2_000_000, "single")
        assert "boom:exact" in caplog.text
        assert "66,665,444,450" in caplog.text
        assert "300,000,000" in caplog.text


@pytest.fixture
def frame_with_a_coarse_trap() -> tuple[pl.DataFrame, list[tuple[int, int, float]]]:
    """200 rows where the coarse column clears `min_reduction` but explodes.

    `coarse` has 20 values x 10 rows -- sample reduction ~0.955, comfortably over
    the 0.90 floor, yet projected to 1M rows it is tens of billions of pairs.
    `dup` is near-unique (singletons plus a handful of true duplicate pairs), so
    it stays cheap under projection. Both have perfect recall on the true pairs,
    which is what forces the decision onto cost.
    """
    n = 200
    df = pl.DataFrame({
        "__row_id__": list(range(n)),
        # 20 runs of 10 CONSECUTIVE rows, so the true pairs below fall inside a
        # run and `coarse` scores recall 1.0 -- the decision then rests on cost
        # alone, which is the thing under test.
        "coarse": [f"c{i // 10}" for i in range(n)],
        "dup": [f"v{i // 2}" if i < 20 else f"u{i}" for i in range(n)],
    })
    pairs = [(i, i + 1, 1.0) for i in range(0, 20, 2)]
    return df, pairs


class TestSelectionUnderBudget:
    def test_omitting_total_rows_is_byte_for_byte_the_old_behaviour(
        self, frame_with_a_coarse_trap
    ):
        """The gate is opt-in on `total_rows`, so every existing caller -- and
        every frame no bigger than its own training sample -- is untouched."""
        df, pairs = frame_with_a_coarse_trap
        without = learn_blocking_rules(df, pairs, min_recall=0.95, min_reduction=0.90)
        same_size = learn_blocking_rules(
            df, pairs, min_recall=0.95, min_reduction=0.90, total_rows=df.height
        )
        assert [r.key() for r in without] == [r.key() for r in same_size]
        assert all(r.projected_pairs == 0 for r in without)

    def test_a_rule_that_clears_min_reduction_can_still_be_rejected(
        self, frame_with_a_coarse_trap, monkeypatch, caplog
    ):
        """THE regression. `coarse:exact` passes both scale-invariant
        constraints and is thrown out purely on projected cost."""
        monkeypatch.setenv("GOLDENMATCH_FS_MAX_PASS_PAIRS", "300000000")
        df, pairs = frame_with_a_coarse_trap

        before = learn_blocking_rules(df, pairs, min_recall=0.95, min_reduction=0.90)
        coarse = next(r for r in before if r.key() == "coarse:exact")
        assert coarse.reduction_ratio >= 0.90, "the trap must clear the ratio floor"

        with caplog.at_level("WARNING"):
            after = learn_blocking_rules(
                df, pairs, min_recall=0.95, min_reduction=0.90, total_rows=1_000_000
            )
        assert "coarse:exact" not in [r.key() for r in after]
        assert "coarse:exact" in caplog.text

    def test_the_surviving_rule_is_the_cheap_one(
        self, frame_with_a_coarse_trap, monkeypatch
    ):
        monkeypatch.setenv("GOLDENMATCH_FS_MAX_PASS_PAIRS", "300000000")
        df, pairs = frame_with_a_coarse_trap
        after = learn_blocking_rules(
            df, pairs, min_recall=0.95, min_reduction=0.90, total_rows=1_000_000
        )
        assert after, "the gate must not empty the rule set when a cheap rule exists"
        assert all(r.projected_pairs <= 300_000_000 for r in after)

    def test_a_total_wipeout_still_returns_a_rule(self, frame_with_a_coarse_trap, monkeypatch):
        """With the budget set below every candidate, the selector degrades to a
        single best-effort rule rather than returning [] -- an empty rule list
        would send `apply_learned_blocks` into a no-blocks run."""
        monkeypatch.setenv("GOLDENMATCH_FS_MAX_PASS_PAIRS", "1")
        df, pairs = frame_with_a_coarse_trap
        after = learn_blocking_rules(
            df, pairs, min_recall=0.95, min_reduction=0.90, total_rows=1_000_000
        )
        assert len(after) == 1

    def test_a_wipeout_picks_the_cheapest_of_the_best_recall_rules(
        self, frame_with_a_coarse_trap, monkeypatch
    ):
        """The old fallback was `max(recall)` alone, which is how an
        unaffordable rule got returned in the first place. Recall still wins;
        cost only breaks the tie."""
        monkeypatch.setenv("GOLDENMATCH_FS_MAX_PASS_PAIRS", "1")
        df, pairs = frame_with_a_coarse_trap
        [chosen] = learn_blocking_rules(
            df, pairs, min_recall=0.95, min_reduction=0.90, total_rows=1_000_000
        )
        every = learn_blocking_rules(
            df, pairs, min_recall=0.0, min_reduction=0.0, total_rows=1_000_000
        )
        best_recall = max(r.recall for r in every)
        cheapest = min(r.projected_pairs for r in every if r.recall == best_recall)
        assert chosen.recall == best_recall
        assert chosen.projected_pairs == cheapest


def test_a_rule_with_two_predicates_on_one_field_can_be_evaluated():
    """Found while wiring the budget: this raised polars' DuplicateError.

    `evaluate_rule` selected `["__row_id__"] + [p.field for p in predicates]`,
    which names the same column twice for a rule like
    `last:exact AND last:soundex`. `learn_blocking_rules` deliberately produces
    those -- its combo guard compares field AND transform, and
    `lower_rule_to_key` documents collapsing them as the #1826 footgun -- so the
    crash was reachable before this change too. It stayed latent because the
    depth-2 search only runs when NO single predicate passes; the pair budget
    can now empty that set on a narrow frame and walk straight into it.
    """
    df = pl.DataFrame({"__row_id__": [0, 1, 2, 3], "name": ["ann", "ann", "bob", "bob"]})
    rule = BlockingRule(predicates=[
        BlockingPredicate(field="name", transform="exact"),
        BlockingPredicate(field="name", transform="first_3"),
    ])
    from goldenmatch.core.learned_blocking import evaluate_rule

    recall, reduction, n_blocks = evaluate_rule(df, rule, {(0, 1), (2, 3)})
    assert recall == 1.0
    assert n_blocks == 2


class TestCachePersistence:
    def test_projected_cost_survives_a_round_trip(self, tmp_path):
        """A cache is reused verbatim on a later run, so the number it was
        accepted at is the only record that it ever was."""
        path = tmp_path / "rules.json"
        rule = _rule("a", pairs=1_234_567)
        rule.projected_max_block = 890
        save_learned_rules([rule], path)
        [loaded] = load_learned_rules(path)
        assert loaded.projected_pairs == 1_234_567
        assert loaded.projected_max_block == 890

    def test_a_cache_written_before_2474_still_loads(self, tmp_path):
        path = tmp_path / "old.json"
        path.write_text(json.dumps([{
            "predicates": [{"field": "a", "transform": "exact"}],
            "recall": 1.0, "reduction_ratio": 0.99, "n_blocks": 7,
        }]))
        [loaded] = load_learned_rules(path)
        assert loaded.n_blocks == 7
        assert loaded.projected_pairs == 0
