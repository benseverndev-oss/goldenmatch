"""Issue #1839 -- auto-config commits LOWERED blocking instead of strategy="learned".

``strategy="learned"`` defers rule learning to runtime inside build_blocks, which
forfeits the bucket scorer for every zero-config run >= 50K rows: the routing
decision (``_use_bucket_scorer``) happens BEFORE build_blocks, and bucket derives
its own buckets from ``passes``/``keys`` rather than calling build_blocks at all.
So runtime-learned rules arrive too late to matter -- the rules must be in the
config by routing time.

These tests pin the wiring AND its fallbacks. The fallbacks matter more than the
happy path: a lowering that silently changed candidate pairs would be the exact
failure mode (#1800 / #1837 / #1839) this work exists to remove.
"""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.autoconfig import _lower_learned_blocking
from goldenmatch.core.learned_blocking import (
    BlockingPredicate,
    BlockingRule,
    LoweringUnsupportedError,
)


def _frame(n: int = 600) -> pl.DataFrame:
    """Small but non-degenerate: real duplicate pairs so the learner finds
    scored pairs, spread across soundex codes so nothing mega-blocks."""
    firsts = ["John", "Jane", "Bob", "Mary", "Ann", "Paul", "Rita", "Sam"]
    lasts = ["Smith", "Jones", "Brown", "Lee", "Clark", "Davis", "Evans", "Ford"]
    cities = ["Boston", "Newark", "Chicago", "Denver", "Austin", "Fresno"]
    rows = []
    for i in range(n // 2):
        f, s, c = firsts[i % 8], lasts[i % 8], cities[i % 6]
        rows.append({"first": f, "last": f"{s}{i}", "city": c})
        rows.append({"first": f, "last": f"{s}{i}", "city": c})  # its duplicate
    return pl.DataFrame(rows).with_row_index("__row_id__")


def _blocking() -> BlockingConfig:
    return BlockingConfig(
        strategy="learned",
        keys=[BlockingKeyConfig(fields=["last"], transforms=["lowercase"])],
        learned_sample_size=200,
        learned_min_recall=0.95,
        skip_oversized=True,
        max_block_size=5000,
    )


class TestLoweringWiring:
    def test_lowers_to_multi_pass_when_it_can(self):
        out = _lower_learned_blocking(_blocking(), _frame(), 100_000)
        assert out.strategy == "multi_pass", "should not still be deferring to runtime"
        assert out.passes, "multi_pass with no passes would fail validation downstream"

    def test_preserves_the_caps_it_was_given(self):
        blk = _blocking()
        blk.max_block_size = 12_345
        out = _lower_learned_blocking(blk, _frame(), 100_000)
        if out.strategy == "multi_pass":  # only meaningful when lowering fired
            assert out.max_block_size == 12_345
            assert out.skip_oversized is True

    def test_kill_switch_keeps_legacy_path(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_LEARNED_LOWERING", "0")
        out = _lower_learned_blocking(_blocking(), _frame(), 100_000)
        assert out.strategy == "learned"


class TestFallsBackNeverRegresses:
    """Every failure path must return strategy='learned' -- i.e. exactly today's
    behavior. Forfeiting bucket is a perf cost; lowering wrong is a silent
    correctness cost. Always take the former."""

    def test_falls_back_when_no_rules_can_be_learned(self, monkeypatch):
        monkeypatch.setattr(
            "goldenmatch.core.blocker.learn_rules_for_frame", lambda lf, cfg: []
        )
        out = _lower_learned_blocking(_blocking(), _frame(), 100_000)
        assert out.strategy == "learned"

    def test_falls_back_on_unlowerable_rule(self, monkeypatch):
        """A same-field conjunction (last:exact AND last:soundex) has no exact
        multi_pass form -- field_transforms is keyed by field. learn_blocking_rules
        CAN emit these, so this is a live path, not a hypothetical."""
        rule = BlockingRule(predicates=[
            BlockingPredicate("last", "exact"), BlockingPredicate("last", "soundex"),
        ])
        monkeypatch.setattr(
            "goldenmatch.core.blocker.learn_rules_for_frame", lambda lf, cfg: [rule]
        )
        out = _lower_learned_blocking(_blocking(), _frame(), 100_000)
        assert out.strategy == "learned"

    def test_falls_back_on_unexpected_error(self, monkeypatch):
        """Lowering is an optimization; it must never take down a config build."""
        def boom(lf, cfg):
            raise RuntimeError("learner exploded")
        monkeypatch.setattr("goldenmatch.core.blocker.learn_rules_for_frame", boom)
        out = _lower_learned_blocking(_blocking(), _frame(), 100_000)
        assert out.strategy == "learned"

    def test_lowering_error_is_not_swallowed_into_a_wrong_config(self, monkeypatch):
        """Guard the shape of the fallback: on LoweringUnsupportedError we must
        return the ORIGINAL learned config, not a half-built multi_pass."""
        def boom(rules, **kw):
            raise LoweringUnsupportedError("nope")
        monkeypatch.setattr(
            "goldenmatch.core.learned_blocking.lower_rules_to_blocking_config", boom
        )
        blk = _blocking()
        out = _lower_learned_blocking(blk, _frame(), 100_000)
        assert out.strategy == "learned"
        assert out.keys == blk.keys


class TestBucketPayoff:
    """The point of the whole exercise."""

    def test_lowered_config_reaches_the_bucket_scorer(self):
        from goldenmatch.config.schemas import (
            GoldenMatchConfig,
            MatchkeyConfig,
            MatchkeyField,
        )
        from goldenmatch.core.pipeline import _use_bucket_scorer

        df = _frame()
        lowered = _lower_learned_blocking(_blocking(), df, 100_000)
        if lowered.strategy != "multi_pass":
            pytest.skip("lowering did not fire on this fixture; covered elsewhere")

        def cfg(blk):
            return GoldenMatchConfig(
                matchkeys=[MatchkeyConfig(
                    name="mk", type="weighted", threshold=0.9,
                    fields=[MatchkeyField(field="last", scorer="jaro_winkler", weight=1.0)],
                )],
                blocking=blk,
            )

        assert _use_bucket_scorer(cfg(_blocking()), df) is False  # before
        assert _use_bucket_scorer(cfg(lowered), df) is True       # after
