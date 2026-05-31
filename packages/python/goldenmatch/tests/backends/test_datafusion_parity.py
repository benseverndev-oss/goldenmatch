"""Parity test: DataFusion backend vs parallel backend.

Spike Day 2 deliverable from
``docs/superpowers/specs/2026-05-30-datafusion-backend-spike-design.md``.

Both backends must produce the IDENTICAL pair set (Jaccard == 1.0)
on the spike's supported matchkey shape: single-field weighted with
scorer in {jaro_winkler, levenshtein, token_sort}. Scores may differ
by float-tolerance epsilon; pair membership and threshold filtering
must match exactly.

Skipped when ``datafusion`` or ``goldenmatch._native`` aren't
installed -- the spike requires both. Surfacing as skip rather than
fail keeps default CI green; the dedicated bench job will hard-fail
if either is missing.
"""
from __future__ import annotations

import pytest

datafusion = pytest.importorskip("datafusion")
native = pytest.importorskip("goldenmatch._native")

import polars as pl

from goldenmatch.backends.datafusion_backend import score_blocks_datafusion
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.blocker import BlockResult
from goldenmatch.core.scorer import score_blocks_parallel


def _make_block(records: list[tuple[int, str]], block_key: str = "k0") -> BlockResult:
    """Build a single BlockResult from (row_id, name) tuples."""
    df = pl.LazyFrame({
        "__row_id__": [r[0] for r in records],
        "__source__": ["fixture"] * len(records),
        "name": [r[1] for r in records],
    })
    return BlockResult(block_key=block_key, df=df)


def _make_mk(scorer: str = "jaro_winkler", threshold: float = 0.85) -> MatchkeyConfig:
    return MatchkeyConfig(
        name="test_mk",
        type="weighted",
        fields=[MatchkeyField(field="name", scorer=scorer, weight=1.0)],
        threshold=threshold,
    )


def _canonical_pairs(pairs: list[tuple[int, int, float]]) -> set[tuple[int, int]]:
    """Strip scores, canonicalize as (min, max)."""
    return {(min(a, b), max(a, b)) for a, b, _ in pairs}


# ---------------------------------------------------------------------------
# Pair-set parity (the gate the spike depends on)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scorer", ["jaro_winkler", "levenshtein", "token_sort"])
def test_pair_set_parity_single_block(scorer):
    """Single block, mixed similarity. Pair set must match exactly."""
    records = [
        (1, "John Smith"),
        (2, "Jon Smith"),     # ~0.97 jw vs 1
        (3, "Jane Smith"),    # ~0.91 jw vs 1
        (4, "John Smyth"),    # ~0.96 jw vs 1
        (5, "Bob Jones"),     # low vs all
        (6, "John Smith"),    # exact dup of 1
    ]
    block = _make_block(records)
    mk = _make_mk(scorer=scorer, threshold=0.85)

    par_pairs = score_blocks_parallel([block], mk, matched_pairs=set())
    df_pairs = score_blocks_datafusion([block], mk, matched_pairs=set())

    par_set = _canonical_pairs(par_pairs)
    df_set = _canonical_pairs(df_pairs)

    assert df_set == par_set, (
        f"scorer={scorer}: pair sets differ. "
        f"parallel-only={par_set - df_set}, datafusion-only={df_set - par_set}"
    )


def test_pair_set_parity_multi_block():
    """Multiple blocks, scored independently. Verifies the
    per-block iteration shape doesn't drop pairs at block boundaries."""
    block_a = _make_block(
        [(10, "Alice Anderson"), (11, "Alice Andersen"), (12, "Bob Brown")],
        block_key="block_a",
    )
    block_b = _make_block(
        [(20, "Charlie Chen"), (21, "Charlie Chan"), (22, "Diane Day")],
        block_key="block_b",
    )
    mk = _make_mk(threshold=0.85)

    par_pairs = score_blocks_parallel([block_a, block_b], mk, matched_pairs=set())
    df_pairs = score_blocks_datafusion([block_a, block_b], mk, matched_pairs=set())

    assert _canonical_pairs(df_pairs) == _canonical_pairs(par_pairs)


def test_pair_set_parity_with_excluded():
    """``matched_pairs`` exclude must hide pairs identically."""
    records = [
        (1, "John Smith"),
        (2, "Jon Smith"),
        (3, "John Smyth"),
    ]
    block = _make_block(records)
    mk = _make_mk(threshold=0.85)

    # Pre-exclude pair (1, 2). Both backends must omit it.
    par_pairs = score_blocks_parallel([block], mk, matched_pairs={(1, 2)})
    df_pairs = score_blocks_datafusion([block], mk, matched_pairs={(1, 2)})

    par_set = _canonical_pairs(par_pairs)
    df_set = _canonical_pairs(df_pairs)
    assert (1, 2) not in par_set, "parallel should respect matched_pairs"
    assert (1, 2) not in df_set, "datafusion should respect matched_pairs"
    assert df_set == par_set


def test_empty_blocks_returns_empty():
    """Empty input is a contract, not a degenerate case."""
    mk = _make_mk()
    assert score_blocks_datafusion([], mk, matched_pairs=set()) == []


def test_single_record_block_returns_empty():
    """Block with one record can't produce any pairs."""
    block = _make_block([(1, "John Smith")])
    mk = _make_mk()
    assert score_blocks_datafusion([block], mk, matched_pairs=set()) == []


# ---------------------------------------------------------------------------
# Score-value parity (float-tolerance — same kernel, should be exact)
# ---------------------------------------------------------------------------


def test_score_values_within_tolerance():
    """For each shared pair, the score must agree within f32-precision
    tolerance.

    Both backends use the same underlying jaro_winkler math, but the
    storage precision differs: the parallel scorer routes through
    ``rapidfuzz.cdist`` which returns f32 columns, while the
    DataFusion path calls ``_native.jaro_winkler_similarity`` per pair
    and gets f64 back. 1e-6 catches any genuine math drift while
    accepting the f32 quantization on parallel's side.
    """
    records = [(1, "John Smith"), (2, "Jon Smith"), (3, "John Smyth")]
    block = _make_block(records)
    mk = _make_mk()

    par_by_pair = {
        (min(a, b), max(a, b)): s
        for a, b, s in score_blocks_parallel([block], mk, matched_pairs=set())
    }
    df_by_pair = {
        (min(a, b), max(a, b)): s
        for a, b, s in score_blocks_datafusion([block], mk, matched_pairs=set())
    }

    assert set(par_by_pair) == set(df_by_pair), "different pair sets"
    for pair, par_score in par_by_pair.items():
        df_score = df_by_pair[pair]
        assert abs(par_score - df_score) < 1e-6, (
            f"score drift on {pair}: parallel={par_score}, datafusion={df_score}"
        )


# ---------------------------------------------------------------------------
# Out-of-spike-scope shapes must raise (NOT silently fall back)
# ---------------------------------------------------------------------------


def test_multi_field_matchkey_raises_not_implemented():
    """Spike covers single-field only. Multi-field weighted must
    raise so callers route to a different backend deliberately."""
    block = _make_block([(1, "John"), (2, "Jon")])
    mk = MatchkeyConfig(
        name="multi",
        type="weighted",
        fields=[
            MatchkeyField(field="name", scorer="jaro_winkler", weight=0.5),
            MatchkeyField(field="name", scorer="levenshtein", weight=0.5),
        ],
        threshold=0.85,
    )
    with pytest.raises(NotImplementedError, match="single-field"):
        score_blocks_datafusion([block], mk, matched_pairs=set())


def test_unsupported_scorer_raises_not_implemented():
    """Embedding / record_embedding / ensemble are out of scope."""
    block = _make_block([(1, "John"), (2, "Jon")])
    mk = MatchkeyConfig(
        name="emb",
        type="weighted",
        fields=[MatchkeyField(field="name", scorer="ensemble", weight=1.0)],
        threshold=0.85,
    )
    with pytest.raises(NotImplementedError, match="scorers"):
        score_blocks_datafusion([block], mk, matched_pairs=set())


def test_exact_matchkey_raises_not_implemented():
    """Spike covers weighted only; exact matchkeys go through a
    different scoring path entirely (find_exact_matches)."""
    block = _make_block([(1, "John"), (2, "John")])
    mk = MatchkeyConfig(
        name="ex",
        type="exact",
        fields=[MatchkeyField(field="name")],
    )
    with pytest.raises(NotImplementedError, match="weighted"):
        score_blocks_datafusion([block], mk, matched_pairs=set())
