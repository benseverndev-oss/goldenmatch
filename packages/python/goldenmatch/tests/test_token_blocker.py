"""DF-pruned token blocker + config tests (#2488)."""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    TokenBlockingConfig,
)
from goldenmatch.core.blocker import build_blocks
from goldenmatch.core.token_blocker import (
    _MAX_DERIVED_DF_CAP,
    _MIN_DERIVED_DF_CAP,
    TokenBlocker,
    build_token_blocks,
    resolve_max_df,
)

# ---- config validation ----


def test_min_token_length_must_be_positive():
    with pytest.raises(ValueError):
        TokenBlockingConfig(column="t", min_token_length=0)


def test_max_df_ratio_must_be_a_fraction():
    for bad in (0.0, -0.1, 1.5):
        with pytest.raises(ValueError):
            TokenBlockingConfig(column="t", max_df_ratio=bad)
    TokenBlockingConfig(column="t", max_df_ratio=1.0)  # inclusive upper bound


def test_max_df_below_two_is_rejected():
    """A token in one record blocks nothing, so a cap of 1 is a silent no-op
    config rather than a tight one."""
    with pytest.raises(ValueError):
        TokenBlockingConfig(column="t", max_df=1)
    TokenBlockingConfig(column="t", max_df=2)


def test_blockingconfig_token_requires_token_block():
    with pytest.raises(ValueError):
        BlockingConfig(strategy="token")


def test_blockingconfig_token_rejects_keys():
    with pytest.raises(ValueError):
        BlockingConfig(
            strategy="token",
            token=TokenBlockingConfig(column="t"),
            keys=[BlockingKeyConfig(fields=["t"])],
        )


# ---- DF cap derivation ----


def test_explicit_max_df_wins_verbatim():
    cfg = TokenBlockingConfig(column="t", max_df=7, max_df_ratio=0.5)
    assert resolve_max_df(cfg, 1_000_000) == 7


def test_derived_cap_tracks_the_ratio_in_the_normal_range():
    cfg = TokenBlockingConfig(column="t", max_df_ratio=0.02)
    assert resolve_max_df(cfg, 4589) == 91  # the Amazon-Google frame


def test_derived_cap_is_bounded_above_so_large_frames_stay_affordable():
    """The ratio alone does not bound cost: 2% of 10M is a 200k-record block,
    i.e. 2e10 pairs from a single token."""
    cfg = TokenBlockingConfig(column="t", max_df_ratio=0.02)
    assert resolve_max_df(cfg, 10_000_000) == _MAX_DERIVED_DF_CAP


def test_derived_cap_is_bounded_below_so_small_frames_keep_tokens():
    """At n=200 a 2% ratio caps DF at 4, which discards nearly every usable
    token. The floor keeps small frames blockable."""
    cfg = TokenBlockingConfig(column="t", max_df_ratio=0.02)
    assert resolve_max_df(cfg, 200) == _MIN_DERIVED_DF_CAP


# ---- tokenization ----


def test_tokens_are_lowercased_length_filtered_and_deduped():
    b = TokenBlocker(min_token_length=3, max_df=100)
    assert b.tokens("Red WIDGET red a bc widget") == {"red", "widget"}


def test_repeated_token_does_not_double_index_the_record():
    """A set, not a list -- otherwise a title repeating a word puts the record
    into that block twice and inflates the pair count."""
    b = TokenBlocker(min_token_length=3, max_df=100)
    idx = b.index(["alpha alpha alpha", "alpha beta"])
    assert idx["alpha"] == [0, 1]


# ---- indexing / pruning ----


def test_singleton_tokens_are_dropped():
    b = TokenBlocker(min_token_length=3, max_df=100)
    idx = b.index(["unique_aaa shared", "unique_bbb shared"])
    assert "shared" in idx
    assert "unique_aaa" not in idx and "unique_bbb" not in idx


def test_tokens_above_the_df_cap_are_pruned():
    """The whole point: a token in every record is a mega-block carrying no
    evidence. Here 'common' has DF 4 and is dropped at max_df=3, while 'rare'
    (DF 2) survives."""
    b = TokenBlocker(min_token_length=3, max_df=3)
    idx = b.index(["common rare", "common rare", "common xxx", "common yyy"])
    assert "common" not in idx
    assert idx["rare"] == [0, 1]


def test_a_record_lands_in_many_blocks():
    """The property exact keys cannot express: one record, several blocks, so a
    pair is a candidate when it shares ANY token."""
    b = TokenBlocker(min_token_length=3, max_df=100)
    idx = b.index(["alpha beta", "alpha gamma", "beta gamma"])
    assert sorted(idx) == ["alpha", "beta", "gamma"]
    assert idx["alpha"] == [0, 1] and idx["beta"] == [0, 2] and idx["gamma"] == [1, 2]


def test_empty_and_short_text_blocks_nothing():
    b = TokenBlocker(min_token_length=3, max_df=100)
    assert b.index(["", "   ", "a b"]) == {}


# ---- blocker contract ----


def _frame():
    return pl.DataFrame({
        "title": [
            "apple macbook pro laptop",
            "apple macbook air laptop",
            "dell inspiron desktop tower",
            "zzz",
        ],
    }).with_row_index("__row_id__")


def test_build_token_blocks_emits_blockresults_preserving_row_ids():
    cfg = BlockingConfig(strategy="token", token=TokenBlockingConfig(column="title", max_df=3))
    blocks = build_token_blocks(_frame().lazy(), cfg)
    by_key = {b.block_key: sorted(b.df.collect()["__row_id__"].to_list()) for b in blocks}
    assert by_key["token_macbook"] == [0, 1]
    assert by_key["token_laptop"] == [0, 1]
    assert all(b.strategy == "token" for b in blocks)


def test_unmatchable_row_appears_in_no_block():
    cfg = BlockingConfig(strategy="token", token=TokenBlockingConfig(column="title", max_df=3))
    blocks = build_token_blocks(_frame().lazy(), cfg)
    all_rows = {r for b in blocks for r in b.df.collect()["__row_id__"].to_list()}
    assert 3 not in all_rows  # "zzz" is below min_token_length


def test_missing_column_raises():
    cfg = BlockingConfig(strategy="token", token=TokenBlockingConfig(column="nope"))
    with pytest.raises(ValueError, match="not found in data"):
        build_token_blocks(_frame().lazy(), cfg)


def test_build_blocks_dispatches_strategy_token():
    """The dispatch wiring in blocker.build_blocks, not just the helper."""
    cfg = BlockingConfig(strategy="token", token=TokenBlockingConfig(column="title", max_df=3))
    blocks = build_blocks(_frame().lazy(), cfg)
    assert blocks and all(b.strategy == "token" for b in blocks)
    assert any(b.block_key == "token_macbook" for b in blocks)


# ---- the property that motivated the strategy (#2488) ----


def test_token_blocking_finds_pairs_an_exact_prefix_key_cannot():
    """The Amazon-Google shape in miniature: two listings for one product whose
    titles lead with different words. Any prefix/soundex key puts them in
    different blocks; a shared discriminative token puts them together.
    """
    df = pl.DataFrame({
        "title": [
            "adobe photoshop elements 9",
            "photoshop elements 9 adobe systems",
        ],
    }).with_row_index("__row_id__")

    prefix_keys = {t[:5] for t in df["title"].to_list()}
    assert len(prefix_keys) == 2, "prefixes differ, so an exact prefix key misses the pair"

    cfg = BlockingConfig(strategy="token", token=TokenBlockingConfig(column="title", max_df=2))
    blocks = build_token_blocks(df.lazy(), cfg)
    paired = [b for b in blocks if sorted(b.df.collect()["__row_id__"].to_list()) == [0, 1]]
    assert paired, "token blocking should co-block the pair"
