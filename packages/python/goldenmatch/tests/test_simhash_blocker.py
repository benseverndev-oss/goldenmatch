"""SimHash/LSH blocker + config tests (#1082).

The blocker tests inject embeddings DIRECTLY (small ``(n, dim)`` float64 arrays
where rows 0,1 are cosine-near and row 2 is orthogonal) — no real embedder /
model is loaded, so the tests are fast and deterministic. The end-to-end embed
path (``build_simhash_blocks``) is exercised with a stubbed embedder.
"""
from __future__ import annotations

import numpy as np
import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    SimHashKeyConfig,
)
from goldenmatch.core.blocker import build_blocks
from goldenmatch.core.simhash_blocker import SimHashLSHBlocker

# ---- config validation (mirrors the LSHKeyConfig suite) ----


def test_simhashkeyconfig_requires_threshold_or_bands():
    with pytest.raises(ValueError):
        SimHashKeyConfig(column="t")


def test_simhashkeyconfig_num_bands_must_divide_num_planes():
    with pytest.raises(ValueError):
        SimHashKeyConfig(column="t", num_planes=256, num_bands=7)
    # divisible is fine
    SimHashKeyConfig(column="t", num_planes=256, num_bands=32)


def test_simhashkeyconfig_threshold_range():
    with pytest.raises(ValueError):
        SimHashKeyConfig(column="t", threshold=0.0)
    with pytest.raises(ValueError):
        SimHashKeyConfig(column="t", threshold=1.0)
    SimHashKeyConfig(column="t", threshold=0.5)


def test_simhashkeyconfig_num_planes_positive():
    with pytest.raises(ValueError):
        SimHashKeyConfig(column="t", num_planes=0, num_bands=1)


def test_blockingconfig_simhash_requires_simhash_block():
    with pytest.raises(ValueError):
        BlockingConfig(strategy="simhash")


def test_blockingconfig_simhash_rejects_keys():
    with pytest.raises(ValueError):
        BlockingConfig(
            strategy="simhash",
            simhash=SimHashKeyConfig(column="t", threshold=0.5),
            keys=[BlockingKeyConfig(fields=["t"])],
        )


def test_blockingconfig_simhash_valid():
    cfg = BlockingConfig(
        strategy="simhash", simhash=SimHashKeyConfig(column="t", threshold=0.5)
    )
    assert cfg.simhash is not None and cfg.simhash.column == "t"


def test_simhashkeyconfig_reexported_from_package():
    import goldenmatch

    assert goldenmatch.SimHashKeyConfig is SimHashKeyConfig


# ---- blocker behavior (embeddings injected directly, no real model) ----


def _near_dup_embeddings() -> np.ndarray:
    """Rows 0,1 are cosine-near (high similarity); row 2 is orthogonal.

    A many-dimensional shared direction for 0,1 (so they project to the same
    SimHash signature across the random planes) and an orthogonal direction for
    row 2 (so it lands elsewhere).
    """
    dim = 64
    base = np.zeros(dim, dtype=np.float64)
    base[:32] = 1.0  # shared "first-half" direction
    near = base.copy()
    near[0] = 0.95  # tiny perturbation -> still cosine-near
    near[5] = 1.05
    ortho = np.zeros(dim, dtype=np.float64)
    ortho[32:] = 1.0  # disjoint "second-half" direction (cosine 0 with base)
    return np.vstack([base, near, ortho])


def _blocker() -> SimHashLSHBlocker:
    # 256 planes, 16 bands of 16 bits each — wide bands so a near-dup pair
    # (Hamming ~8/256) collides in at least one band while the orthogonal row
    # (Hamming ~130/256) does not.
    return SimHashLSHBlocker.from_config(
        SimHashKeyConfig(column="t", num_planes=256, num_bands=16, seed=0)
    )


def test_candidate_pairs_groups_near_dups_not_orthogonal():
    emb = _near_dup_embeddings()
    pairs = _blocker().candidate_pairs(emb)
    assert (0, 1) in pairs  # cosine-near rows collide
    assert (0, 2) not in pairs  # orthogonal row does not join
    assert (1, 2) not in pairs


def test_candidate_pairs_dedups_across_bands():
    # Identical embeddings collide in every band; the pair is offered once.
    row = np.zeros(32, dtype=np.float64)
    row[:16] = 1.0
    emb = np.vstack([row, row])
    pairs = SimHashLSHBlocker.from_config(
        SimHashKeyConfig(column="t", num_planes=256, num_bands=16, seed=0)
    ).candidate_pairs(emb)
    assert pairs == {(0, 1)}


def test_zero_embedding_rows_excluded():
    # An all-zero embedding has no direction; it must never be paired.
    dim = 32
    a = np.zeros(dim, dtype=np.float64)
    a[:16] = 1.0
    b = a.copy()
    zero = np.zeros(dim, dtype=np.float64)
    emb = np.vstack([a, b, zero])
    pairs = SimHashLSHBlocker.from_config(
        SimHashKeyConfig(column="t", num_planes=256, num_bands=16, seed=0)
    ).candidate_pairs(emb)
    assert (0, 1) in pairs
    assert all(2 not in p for p in pairs)  # the zero row never pairs


def test_blocks_emits_simhash_lsh_blockresults():
    emb = _near_dup_embeddings()
    df = pl.DataFrame({"__row_id__": [0, 1, 2], "t": ["a", "b", "c"]})
    blocks = _blocker().blocks(df, emb)
    assert blocks, "expected at least one SimHash block"
    found_pair = False
    for blk in blocks:
        members = blk.df.collect()["__row_id__"].to_list()
        assert len(members) >= 2  # non-singleton blocks only
        assert blk.strategy == "simhash_lsh"
        if 0 in members and 1 in members:
            found_pair = True
    assert found_pair  # the near-dup pair shares a block


# ---- end-to-end dispatch via build_blocks with a stubbed embedder ----


def test_build_blocks_dispatch_simhash(monkeypatch):
    """`build_blocks` routes strategy='simhash' through the embed path.

    The embedder is stubbed so no model is loaded — it returns the injected
    near-dup embeddings keyed by row order.
    """
    emb = _near_dup_embeddings()
    df = pl.DataFrame({"__row_id__": [0, 1, 2], "t": ["row a", "row b", "row c"]})

    class _StubEmbedder:
        def embed_column(self, values, cache_key):
            assert len(values) == 3
            return emb

    monkeypatch.setattr(
        "goldenmatch.core.embedder.get_embedder", lambda *a, **k: _StubEmbedder()
    )

    cfg = BlockingConfig(
        strategy="simhash",
        simhash=SimHashKeyConfig(column="t", num_planes=256, num_bands=16, seed=0),
    )
    blocks = build_blocks(df.lazy(), cfg)
    assert blocks
    found_pair = False
    for blk in blocks:
        members = blk.df.collect()["__row_id__"].to_list()
        assert blk.strategy == "simhash_lsh"
        if 0 in members and 1 in members:
            found_pair = True
    assert found_pair


def test_missing_column_raises():
    from goldenmatch.core.simhash_blocker import build_simhash_blocks

    df = pl.DataFrame({"other": ["a", "b"]})
    cfg = BlockingConfig(
        strategy="simhash", simhash=SimHashKeyConfig(column="t", threshold=0.5)
    )
    with pytest.raises(ValueError):
        build_simhash_blocks(df.lazy(), cfg)
