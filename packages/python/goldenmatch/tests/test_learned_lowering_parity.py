"""Issue #1839 -- lowering learned rules into a bucket-eligible multi_pass config.

Zero-config runs >= 50K rows set ``strategy="learned"``; ``_use_bucket_scorer``
refuses ``learned``, so the DEFAULT path at scale forfeits the bucket scorer and
pays the legacy per-block path. That is a REPRESENTATION gap, not a semantic one:
bucket derives keys from ``blocking.passes/keys`` and a learned config carries
neither. Lowering the rules into ``multi_pass`` + ``field_transforms`` (#1826)
closes it.

Nothing here is wired up -- no default changes. These tests answer the gating
question with data. See also ``scripts/learned_lowering_diff.py``.

The tests split deliberately into two kinds:

* PARITY tests assert the lowering is exact where it must be.
* CHARACTERIZATION tests pin the two known divergences WITHOUT asserting either
  side is correct. Each is a recall/cost tradeoff previously settled by
  measurement (PR #390: dropping empty keys "lost 3 records on the cross-file
  dedupe regression suite"), and they must be settled the same way -- not by
  argument. They are recorded here so a future change can't move them silently.
"""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.blocker import build_blocks
from goldenmatch.core.learned_blocking import (
    _LOWERED_CHAIN,
    _TRANSFORM_MAP,
    BlockingPredicate,
    BlockingRule,
    LoweringUnsupportedError,
    apply_learned_blocks,
    lower_rule_to_key,
    lower_rules_to_blocking_config,
)
from goldenmatch.core.matchkey import _try_native_chain
from goldenmatch.core.pipeline import _use_bucket_scorer
from goldenmatch.utils.transforms import apply_transforms

BIG = 10**9  # take max_block_size out of the picture; we compare semantics


def _p(field: str, transform: str) -> BlockingPredicate:
    return BlockingPredicate(field=field, transform=transform)


def _rule(*preds: BlockingPredicate) -> BlockingRule:
    return BlockingRule(predicates=list(preds))


def _pairs(blocks) -> set[tuple[int, int]]:
    """Candidate pairs implied by a block list. Pairs -- not block identity --
    are what decide recall: two paths can disagree on block SHAPE yet generate
    identical pairs."""
    out: set[tuple[int, int]] = set()
    for b in blocks:
        ids = sorted(b.materialize().native["__row_id__"].to_list())
        for i, a in enumerate(ids):
            for c in ids[i + 1:]:
                out.add((a, c))
    return out


def _both(df: pl.DataFrame, rules: list[BlockingRule]) -> tuple[set, set]:
    learned = _pairs(apply_learned_blocks(df.lazy(), rules, max_block_size=BIG))
    cfg = lower_rules_to_blocking_config(rules, max_block_size=BIG, skip_oversized=False)
    lowered = _pairs(build_blocks(df.lazy(), cfg))
    return learned, lowered


CLEAN = pl.DataFrame({
    "__row_id__": [0, 1, 2, 3, 4, 5],
    "last": ["Smith", "Smyth", "Jones", "Jones", "Brown", "Brown"],
    "city": ["Boston", "Boston", "Newark", "New York", "Chicago", "Chicago"],
})


class TestTransformMapping:
    """Every learned transform must have a value-identical registry chain."""

    SAMPLES = ["Smith Jones 123", "  Padded  Name ", "O'Brien", "de la Cruz",
               "X", "", "12345", "ABC-123-XYZ", "Ünicode Näme", "Mc Donald  Jr"]

    def test_every_learned_transform_is_lowerable(self):
        assert set(_LOWERED_CHAIN) == set(_TRANSFORM_MAP), (
            "a learned transform with no chain would make its rules unlowerable"
        )

    @pytest.mark.parametrize("transform", sorted(_TRANSFORM_MAP))
    def test_chain_is_value_identical(self, transform):
        chain = _LOWERED_CHAIN[transform]
        for s in self.SAMPLES:
            assert apply_transforms(s, chain) == _TRANSFORM_MAP[transform](s), (
                f"{transform} diverges on {s!r}"
            )

    def test_majority_of_chains_are_natively_vectorizable(self):
        """Not a requirement -- a recorded bonus. The lowered config escapes
        map_elements for these, which is a second win on top of reclaiming
        bucket. If this regresses, the lowering got slower, not wronger."""
        native = {t for t, c in _LOWERED_CHAIN.items() if _try_native_chain("f", c) is not None}
        assert native == {"exact", "first_3", "first_5", "digits_only"}


class TestBucketEligibility:
    """The whole point: the lowered config must clear the gate learned fails."""

    @staticmethod
    def _cfg(blocking: BlockingConfig) -> GoldenMatchConfig:
        return GoldenMatchConfig(
            matchkeys=[MatchkeyConfig(
                name="mk", type="weighted", threshold=0.9,
                fields=[MatchkeyField(field="last", scorer="jaro_winkler", weight=1.0)],
            )],
            blocking=blocking,
        )

    def test_learned_is_refused_but_lowered_is_accepted(self):
        df = pl.DataFrame({"__row_id__": [0, 1], "last": ["Smith", "Smyth"],
                           "city": ["Boston", "Boston"]})
        learned = self._cfg(BlockingConfig(strategy="learned", skip_oversized=True))
        lowered = self._cfg(lower_rules_to_blocking_config(
            [_rule(_p("last", "soundex"), _p("city", "first_3"))]))

        assert _use_bucket_scorer(learned, df) is False  # the bug
        assert _use_bucket_scorer(lowered, df) is True   # the fix


class TestLoweringParity:
    """On clean data the lowering must be pair-identical to learned."""

    @pytest.mark.parametrize("label,rules", [
        ("depth-1", [_rule(_p("last", "soundex"))]),
        ("depth-2", [_rule(_p("last", "soundex"), _p("city", "first_3"))]),
        ("multi-rule union", [_rule(_p("last", "soundex")), _rule(_p("city", "first_3"))]),
        ("every transform", [_rule(_p("last", "exact")), _rule(_p("city", "first_3")),
                             _rule(_p("last", "first_5")), _rule(_p("city", "first_token"))]),
    ])
    def test_pairs_identical_on_clean_data(self, label, rules):
        learned, lowered = _both(CLEAN, rules)
        assert learned == lowered, f"{label}: -{learned - lowered} +{lowered - learned}"

    def test_respects_the_same_three_rule_cap_as_apply_learned_blocks(self):
        """apply_learned_blocks uses rules[:3]; a lowering that kept more (or
        fewer) would generate different candidates."""
        rules = [_rule(_p("last", "exact")), _rule(_p("city", "exact")),
                 _rule(_p("last", "soundex")), _rule(_p("city", "first_3"))]
        cfg = lower_rules_to_blocking_config(rules)
        assert len(cfg.passes) == 3
        learned, lowered = _both(CLEAN, rules)
        assert learned == lowered

    def test_emits_multi_pass_union(self):
        cfg = lower_rules_to_blocking_config([_rule(_p("last", "soundex"))])
        assert cfg.strategy == "multi_pass"
        assert cfg.union_mode is True

    def test_carries_per_field_chains_not_a_widened_key(self):
        """The #1826 footgun: collapsing per-field chains into one key-level
        chain widens every field and mega-blocks."""
        key = lower_rule_to_key(_rule(_p("last", "soundex"), _p("city", "first_3")))
        assert key.fields == ["last", "city"]
        assert key.field_transforms == {
            "last": ["soundex"],
            "city": ["strip", "lowercase", "substring:0:3"],
        }


class TestRefusesInexactLowering:
    """Refuse rather than lower approximately. An approximate lowering changes
    which pairs are generated SILENTLY -- precision stays 1.0 and only recall
    moves, the exact shape of #1800 / #1837 / #1839."""

    def test_same_field_conjunction_is_refused(self):
        """field_transforms is keyed by field, so it cannot hold two chains for
        one field. learn_blocking_rules CAN emit these: its guard is
        `p1.key() == p2.key()` (field+transform), not field alone."""
        with pytest.raises(LoweringUnsupportedError, match="multiple predicates on field"):
            lower_rule_to_key(_rule(_p("last", "exact"), _p("last", "soundex")))

    def test_unknown_transform_is_refused(self):
        with pytest.raises(LoweringUnsupportedError, match="no registry chain"):
            lower_rule_to_key(_rule(_p("last", "metaphone")))

    def test_one_bad_rule_refuses_the_whole_config(self):
        """A partial lowering would silently drop that rule's candidate pairs."""
        with pytest.raises(LoweringUnsupportedError):
            lower_rules_to_blocking_config([
                _rule(_p("last", "soundex")),
                _rule(_p("city", "exact"), _p("city", "first_3")),
            ])

    def test_empty_rules_refused(self):
        with pytest.raises(LoweringUnsupportedError):
            lower_rules_to_blocking_config([])


class TestKnownDivergences:
    """CHARACTERIZATION -- not correctness.

    These pin the two edge cases where learned and static disagree. Neither side
    is asserted correct: both are recall/cost tradeoffs that must be settled by
    measurement on real corpora (PR #390 is the precedent), not by argument.
    Recorded so the behavior can't shift silently while we decide.
    """

    EMPTIES = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3, 4],
        "last": ["", "", "", "Smith", "Smith"],
        "city": ["", "", "", "Boston", "Boston"],
    })

    def test_empty_key_depth1_lowered_adds_the_zero_information_block(self):
        """learned drops it ("" is falsy); static keeps it. At 1M this is every
        missing value in one O(n^2) block -- but #390 measured that dropping it
        loses REAL matches, because scoring still compares those rows on their
        other fields."""
        learned, lowered = _both(self.EMPTIES, [_rule(_p("last", "exact"))])
        assert learned == {(3, 4)}
        assert lowered == {(0, 1), (0, 2), (1, 2), (3, 4)}

    def test_empty_key_depth2_learned_drops_lowered_keeps(self):
        """UPDATED (fix/blocking-null-key-filter, #1859): the depth-2 "||"
        truthiness leak is fixed at the source -- ``_compute_block_key`` now
        returns "" when every predicate is empty, so learned DROPS the all-empty
        block at depth 2, matching depth 1.

        This creates a divergence with the LOWERED path: ``filter_valid_key``
        keeps "" (the explicit-empty-cell value, #390), so lowered still blocks
        the all-empty rows. learned cannot tell "" from null (its transforms map
        both to ""), the lowered path can -- so on genuinely-"" data they now
        differ. Recorded, not resolved: settling the ""-vs-null policy uniformly
        is the #1859 umbrella item. The lowering compiler is currently inert
        (its auto-config wiring #1845 was closed as invalid), so no live path
        depends on this equivalence."""
        learned, lowered = _both(self.EMPTIES, [_rule(_p("last", "exact"), _p("city", "exact"))])
        assert learned == {(3, 4)}  # all-empty rows 0,1,2 dropped
        assert (0, 1) in lowered    # lowered keeps "" per #390
        assert learned != lowered   # the new, tracked divergence

    def test_nulls_agree_by_coincidence(self):
        """Both paths drop NULLs, via DIFFERENT mechanisms: static filters
        is_not_null; learned maps None -> "" which is falsy. Same outcome at
        depth 1 -- but it is a coincidence, not a shared rule."""
        nulls = pl.DataFrame({
            "__row_id__": [0, 1, 2, 3],
            "last": [None, None, "Smith", "Smith"],
            "city": [None, None, "Boston", "Boston"],
        })
        learned, lowered = _both(nulls, [_rule(_p("last", "exact"))])
        assert learned == lowered == {(2, 3)}

    def test_sentinel_strings_diverge(self):
        """static filters "nan"/"null"/"none" as stringified-NULL artifacts;
        learned keeps them as literals. If a user genuinely has the string
        "null", static silently drops that pair."""
        sentinels = pl.DataFrame({
            "__row_id__": [0, 1, 2, 3],
            "last": ["null", "null", "Smith", "Smith"],
            "city": ["nan", "nan", "Boston", "Boston"],
        })
        learned, lowered = _both(sentinels, [_rule(_p("last", "exact"))])
        assert (0, 1) in learned
        assert (0, 1) not in lowered
