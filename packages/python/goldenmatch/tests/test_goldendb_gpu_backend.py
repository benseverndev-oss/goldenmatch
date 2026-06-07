"""Tests for the EXPERIMENTAL GoldenDB matrix-native backend (backend='gpu').

Runs on CPU JAX -- validates numerical correctness, the exact GA2M additive
attribution, monotonicity, the gradient-based training step, the block-scorer
contract, pipeline dispatch, and the (id, cluster_id) handoff into the existing
CPU clustering path. GPU wall-clock is NOT validated here (no GPU in CI).
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

pytest.importorskip("jax")

from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.blocker import BlockResult
from goldenmatch.core.goldendb import (
    GA2MCombiner,
    char_ngram_hashed,
    combine_matrices,
    cosine_matrix,
    find_matches_gpu,
    jax_available,
    score_blocks_gpu,
)


def _mk(threshold: float = 0.6) -> MatchkeyConfig:
    return MatchkeyConfig(
        name="m",
        type="weighted",
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
        threshold=threshold,
    )


def _block(df: pl.DataFrame) -> BlockResult:
    return BlockResult(block_key="b", df=df.lazy())


def _people() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 3],
            "name": ["John Smith", "Jon Smith", "Mary Jones", "Mary Jones"],
        }
    )


# ── encoding + cosine (the matmul) ────────────────────────────────────────────

def test_jax_available():
    assert jax_available() is True


def test_encode_cosine_identical_high_different_low():
    mat = char_ngram_hashed(["john smith", "john smith", "zzzzz qqqqq"])
    sim = cosine_matrix(mat)
    assert sim.shape == (3, 3)
    assert sim[0, 1] > 0.99          # identical strings
    assert sim[0, 2] < 0.2           # disjoint strings
    np.testing.assert_allclose(np.diag(sim), 1.0, atol=1e-4)


def test_encode_null_rows_are_zero():
    mat = char_ngram_hashed(["abc", None, ""])
    assert np.all(mat[1] == 0.0)
    assert np.all(mat[2] == 0.0)


# ── GA2M combine: exact attribution + monotonicity ────────────────────────────

def test_combine_attribution_is_exact():
    rng = np.random.default_rng(0)
    sim_stack = rng.random((3, 5, 5)).astype(np.float32)
    weights = np.array([1.0, 2.0, 0.5])
    score, attribution = combine_matrices(sim_stack, weights)
    # The audit: contributions sum EXACTLY to the score.
    np.testing.assert_allclose(score, attribution.sum(axis=0), atol=1e-5)


def test_combine_is_monotone_in_each_field():
    sim_stack = np.full((2, 2, 2), 0.5, dtype=np.float32)
    weights = np.array([1.0, 1.0])
    base, _ = combine_matrices(sim_stack, weights)
    bumped = sim_stack.copy()
    bumped[0, 0, 1] = 0.95
    after, _ = combine_matrices(bumped, weights)
    assert after[0, 1] >= base[0, 1]


def test_combine_null_validity_excludes_field():
    # Field 1 invalid everywhere -> score is driven by field 0 alone.
    sim_stack = np.stack([np.full((2, 2), 0.8), np.full((2, 2), 0.0)]).astype(np.float32)
    weights = np.array([1.0, 1.0])
    valid = np.stack([np.ones((2, 2)), np.zeros((2, 2))]).astype(np.float32)
    score, _ = combine_matrices(sim_stack, weights, valid)
    np.testing.assert_allclose(score[0, 1], 0.8, atol=1e-5)


# ── GA2M trainable combine (differentiable, monotone, probabilistic) ──────────

def test_ga2m_predict_monotone():
    c = GA2MCombiner(n_fields=2)
    low = c.predict(np.array([[0.1, 0.1]]))[0]
    high = c.predict(np.array([[0.9, 0.9]]))[0]
    assert high >= low


def test_ga2m_train_step_reduces_loss():
    rng = np.random.default_rng(0)
    sims = rng.random((256, 2))
    labels = (sims.mean(axis=1) > 0.6).astype(float)
    c = GA2MCombiner(n_fields=2, seed=1)
    loss0 = c.loss(sims, labels)
    for _ in range(300):
        c.train_step(sims, labels, lr=0.05)
    loss1 = c.loss(sims, labels)
    assert loss1 < loss0


def test_ga2m_attribution_sums_to_weighted_average():
    c = GA2MCombiner(n_fields=3)
    sims = np.array([[0.2, 0.8, 0.5]])
    contrib = c.attribution(sims)
    # contributions reconstruct the weighted-average term feeding the link.
    import jax

    w = np.asarray(jax.nn.softplus(jax.numpy.asarray(c.params.w)))
    expected = (sims[0] * w).sum() / (w.sum() + 1e-9)
    np.testing.assert_allclose(contrib.sum(), expected, atol=1e-5)


# ── block scorer contract ─────────────────────────────────────────────────────

def test_find_matches_gpu_finds_duplicate():
    df = pl.DataFrame(
        {"__row_id__": [0, 1, 2], "name": ["John Smith", "Jon Smith", "Zelda Quux"]}
    )
    pairs = find_matches_gpu(df, _mk(0.6))
    assert any({a, b} == {0, 1} for a, b, _s in pairs)
    assert not any(2 in (a, b) for a, b, _s in pairs)
    for a, b, s in pairs:
        assert isinstance(a, int) and isinstance(b, int) and isinstance(s, float)
        assert a < b                      # canonicalised (min, max)
        assert 0.0 <= s <= 1.0


def test_find_matches_gpu_exclude_pairs():
    df = pl.DataFrame(
        {"__row_id__": [0, 1, 2], "name": ["John Smith", "Jon Smith", "Zelda Quux"]}
    )
    pairs = find_matches_gpu(df, _mk(0.6), exclude_pairs={(0, 1)})
    assert all({a, b} != {0, 1} for a, b, _s in pairs)


def test_find_matches_gpu_single_row_empty():
    df = pl.DataFrame({"__row_id__": [0], "name": ["solo"]})
    assert find_matches_gpu(df, _mk()) == []


def test_score_blocks_gpu_contract_and_exclusion():
    blocks = [_block(_people())]
    pairs = score_blocks_gpu(blocks, _mk(0.6), set())
    assert isinstance(pairs, list)
    assert any({a, b} == {0, 1} for a, b, _s in pairs)
    assert any({a, b} == {2, 3} for a, b, _s in pairs)

    excluded = score_blocks_gpu(blocks, _mk(0.6), {(0, 1)})
    assert all({a, b} != {0, 1} for a, b, _s in excluded)


def test_score_blocks_gpu_empty_blocks():
    assert score_blocks_gpu([], _mk(), set()) == []


# ── pipeline dispatch ─────────────────────────────────────────────────────────

def test_get_block_scorer_routes_gpu():
    from goldenmatch.core.pipeline import _get_block_scorer

    assert _get_block_scorer(SimpleNamespace(backend="gpu")) is score_blocks_gpu
    default = _get_block_scorer(SimpleNamespace(backend=None))
    assert default.__name__ == "score_blocks_parallel"


# ── the (id, cluster_id) handoff into the existing CPU clustering path ─────────

def test_gpu_pairs_feed_cpu_clustering():
    """The spec's contract: the GPU emits (id_a, id_b, score); the unchanged CPU
    clustering path turns it into clusters keyed by __row_id__."""
    from goldenmatch.core.cluster import build_clusters

    df = _people()
    pairs = score_blocks_gpu([_block(df)], _mk(0.6), set())
    clusters = build_clusters(pairs, df["__row_id__"].to_list())
    member_sets = [set(c["members"]) for c in clusters.values()]
    assert {0, 1} in member_sets
    assert {2, 3} in member_sets
