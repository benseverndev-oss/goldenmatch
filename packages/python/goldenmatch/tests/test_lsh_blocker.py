"""MinHash/LSH blocker + config tests (#1081)."""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig, LSHKeyConfig
from goldenmatch.core.blocker import build_blocks
from goldenmatch.core.lsh_blocker import MinHashLSHBlocker

# ---- config validation ----


def test_lshkeyconfig_requires_threshold_or_bands():
    with pytest.raises(ValueError):
        LSHKeyConfig(column="t")


def test_lshkeyconfig_num_bands_must_divide_num_perms():
    with pytest.raises(ValueError):
        LSHKeyConfig(column="t", num_perms=128, num_bands=7)
    # divisible is fine
    LSHKeyConfig(column="t", num_perms=128, num_bands=32)


def test_blockingconfig_lsh_requires_lsh_block():
    with pytest.raises(ValueError):
        BlockingConfig(strategy="lsh")


def test_blockingconfig_lsh_rejects_keys():
    with pytest.raises(ValueError):
        BlockingConfig(
            strategy="lsh",
            lsh=LSHKeyConfig(column="t", threshold=0.5),
            keys=[BlockingKeyConfig(fields=["t"])],
        )


def test_blockingconfig_lsh_valid():
    cfg = BlockingConfig(strategy="lsh", lsh=LSHKeyConfig(column="t", threshold=0.5))
    assert cfg.lsh is not None and cfg.lsh.column == "t"


# ---- blocker behavior ----

_BASE = "the quick brown fox jumps over the lazy dog near the river bank today"
_NEAR = "the quick brown fox jumps over the lazy dog beside the river bank today"  # 1-word edit
_DISTINCT_A = "completely unrelated sentence about astrophysics and quantum mechanics"
_DISTINCT_B = "another separate passage entirely different vocabulary and subject matter"


def _blocker() -> MinHashLSHBlocker:
    return MinHashLSHBlocker.from_config(
        LSHKeyConfig(column="t", mode="word", k=2, num_perms=128, threshold=0.4, seed=0)
    )


def test_candidate_pairs_finds_near_duplicate():
    texts = [_BASE, _NEAR, _DISTINCT_A, _DISTINCT_B]
    pairs = _blocker().candidate_pairs(texts)
    assert (0, 1) in pairs  # near-dup recovered
    # zero shared shingles => exactly zero collision probability (0**r == 0)
    assert (0, 2) not in pairs
    assert (0, 3) not in pairs
    assert (2, 3) not in pairs


def test_candidate_pairs_dedups_across_bands():
    # identical texts collide in every band; the pair is still offered once.
    texts = [_BASE, _BASE]
    pairs = _blocker().candidate_pairs(texts)
    assert pairs == {(0, 1)}


def test_empty_and_whitespace_rows_excluded():
    texts = ["hello world foo bar baz", "hello world foo bar baz", "", "   \t"]
    blocker = MinHashLSHBlocker.from_config(
        LSHKeyConfig(column="t", mode="word", k=1, num_perms=64, num_bands=32, seed=0)
    )
    pairs = blocker.candidate_pairs(texts)
    assert (0, 1) in pairs  # identical non-empty -> paired
    assert all(2 not in p and 3 not in p for p in pairs)  # empties never paired


def test_build_blocks_dispatch_lsh():
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2],
            "t": [_BASE, _NEAR, _DISTINCT_A],
        }
    )
    cfg = BlockingConfig(
        strategy="lsh",
        lsh=LSHKeyConfig(column="t", mode="word", k=2, num_perms=128, threshold=0.4, seed=0),
    )
    blocks = build_blocks(df.lazy(), cfg)
    assert blocks, "expected at least one LSH block"
    found_pair = False
    for b in blocks:
        members = b.materialize().native["__row_id__"].to_list()
        assert len(members) >= 2  # non-singleton blocks only
        assert b.strategy == "minhash_lsh"
        if 0 in members and 1 in members:
            found_pair = True
    assert found_pair  # the near-dup pair shares a block


def test_missing_column_raises():
    from goldenmatch.core.lsh_blocker import build_lsh_blocks

    df = pl.DataFrame({"other": ["a", "b"]})
    cfg = BlockingConfig(strategy="lsh", lsh=LSHKeyConfig(column="t", threshold=0.5))
    with pytest.raises(ValueError):
        build_lsh_blocks(df.lazy(), cfg)
