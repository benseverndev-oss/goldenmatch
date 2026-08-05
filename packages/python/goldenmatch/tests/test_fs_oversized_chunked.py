"""Bounded (tiled) scoring of an un-splittable oversized block — #1826 / #2417.

When ``_split_oversized`` cannot split an oversized block and
``skip_oversized=False``, the block used to be handed to the scorer WHOLE: one
call carrying the block's full ``C(n, 2)``. That call allocates ``O(n^2)`` on the
vectorized lane and materializes the whole block's surviving pairs twice on the
native lane, which is what took a 14M-row identity resolve to ~31GB (#2417).

Contracts under test:

1. ``_iter_bounded_tiles`` covers every intra-block pair EXACTLY once (diagonal
   tiles carry the intra-group pairs, cross tiles the straddling ones).
2. ``_score_block_bounded`` output == the whole-block output: same pair set,
   same scores (emission ORDER differs — tile order, not row-major).
3. The gate: a block at or under the pair budget, or at or under
   ``max_block_size``, takes the untouched single call.
   ``GOLDENMATCH_FS_OVERSIZED_CHUNK_PAIRS=0`` restores score-whole.
4. No single scoring call carries more than ~the budget's candidate pairs —
   the property that actually bounds the allocation.
5. End-to-end through ``score_buckets``: an un-splittable hub block scores to
   the same pairs tiled as whole, on both the native and per-block lanes.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.backends.score_buckets import (
    _fs_oversized_pair_budget,
    _iter_bounded_tiles,
    _pair_count,
    _score_block_bounded,
)
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.probabilistic import train_em

HUB_KEY = "HUB"


def _hub_df(n: int = 300) -> pl.DataFrame:
    """An UN-SPLITTABLE oversized block: every row shares the blocking key
    (``zip``), and the name columns are all distinct, so ``_auto_split_block``
    has nothing to split on — the exact #2417 hub shape. Names are near-misses
    of each other so a real fraction of the pairs clears the link threshold."""
    first = ["Jonathan", "Roberta", "Katherine", "Michael", "Elizabeth",
             "Andrew", "Priscilla", "Sebastian", "Gwendolyn", "Nathaniel"]
    last = ["Abernathy", "Blaustein", "Castellano", "Devereaux",
            "Fairweather", "Hollingsworth", "Kensington"]
    return pl.DataFrame(
        {
            "__row_id__": list(range(1, n + 1)),
            # Every value is DISTINCT (the unique suffix), so no column offers a
            # split; rows sharing a stem are still near-identical strings, so
            # the block has real signal instead of a degenerate all-agree EM.
            "first_name": [f"{first[i % len(first)]}{i:04d}" for i in range(n)],
            "last_name": [f"{last[i % len(last)]}{i:04d}" for i in range(n)],
            "zip": [HUB_KEY] * n,
        }
    )


def _mk() -> MatchkeyConfig:
    # Explicit link_threshold: this fixture is deliberately degenerate for EM
    # (one constant blocking key, every value distinct), so pin the cutoff
    # rather than let EM calibration decide whether ANY pair survives.
    return MatchkeyConfig(
        name="fs",
        type="probabilistic",
        link_threshold=0.55,
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3,
                          partial_threshold=0.8),
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=2,
                          partial_threshold=0.85),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ],
    )


def _pairset(pairs) -> set[tuple[int, int]]:
    return {(a, b) if a < b else (b, a) for a, b, _ in pairs}


def _scored(pairs) -> dict[tuple[int, int], float]:
    return {((a, b) if a < b else (b, a)): s for a, b, s in pairs}


# ── 1. tiling covers every pair exactly once ─────────────────────────────────


@pytest.mark.parametrize("n,budget", [(10, 8), (37, 50), (64, 200), (101, 32)])
def test_bounded_tiles_cover_every_pair_exactly_once(n: int, budget: int) -> None:
    df = _hub_df(n)
    covered: list[tuple[int, int]] = []
    for tile, head_len in _iter_bounded_tiles(df, n, budget):
        ids = tile["__row_id__"].to_list()
        if head_len is None:
            covered.extend(
                (ids[i], ids[j])
                for i in range(len(ids))
                for j in range(i + 1, len(ids))
            )
        else:
            head, tail = ids[:head_len], ids[head_len:]
            covered.extend((a, b) for a in head for b in tail)

    canon = [(a, b) if a < b else (b, a) for a, b in covered]
    assert len(canon) == len(set(canon)), "a pair was covered by two tiles"
    assert set(canon) == {
        (a, b) for a in range(1, n + 1) for b in range(a + 1, n + 1)
    }
    assert len(canon) == _pair_count(n)


@pytest.mark.parametrize("n,budget", [(64, 200), (200, 400), (301, 5000)])
def test_no_tile_exceeds_the_pair_budget(n: int, budget: int) -> None:
    """The property that bounds the allocation: no single tile handed to the
    scorer carries more than the budget's candidate pairs."""
    df = _hub_df(n)
    for tile, _head in _iter_bounded_tiles(df, n, budget):
        assert _pair_count(len(tile)) <= budget, (
            f"tile of {len(tile)} rows = {_pair_count(len(tile))} pairs "
            f"exceeds budget {budget}"
        )


# ── 2. tiled output == whole-block output ────────────────────────────────────


def _real_scorer():
    """The shipped per-block FS scorer, bound to a trained EM on the hub."""
    df = _hub_df(400)
    mk = _mk()
    em = train_em(df, mk, n_sample_pairs=500)
    from goldenmatch.core.probabilistic import probabilistic_block_scorer

    return df, probabilistic_block_scorer(mk, em)


def test_tiled_scoring_matches_whole_block(monkeypatch) -> None:
    df, scorer = _real_scorer()
    n = df.height

    def score_one(frame):
        return scorer(frame, None)

    whole = score_one(df)
    # Budget well under C(400,2)=79,800 so tiling definitely engages.
    monkeypatch.setenv("GOLDENMATCH_FS_OVERSIZED_CHUNK_PAIRS", "2000")
    tiled = _score_block_bounded(df, n, score_one, tile_above_rows=100)

    assert _pairset(tiled) == _pairset(whole)
    assert _scored(tiled) == _scored(whole)
    # The whole point: the tiled run really did tile (more calls than one).
    assert len(list(_iter_bounded_tiles(df, n, 2000))) > 1


def test_tiled_scoring_emits_no_duplicate_pairs(monkeypatch) -> None:
    df, scorer = _real_scorer()
    monkeypatch.setenv("GOLDENMATCH_FS_OVERSIZED_CHUNK_PAIRS", "2000")
    tiled = _score_block_bounded(
        df, df.height, lambda f: scorer(f, None), tile_above_rows=100
    )
    canon = [(a, b) if a < b else (b, a) for a, b, _ in tiled]
    assert len(canon) == len(set(canon))


# ── 3. the gate ──────────────────────────────────────────────────────────────


def test_block_at_or_under_max_block_size_is_one_untouched_call(monkeypatch) -> None:
    """Auto-split sub-blocks land at or under ``max_block_size``; they must keep
    the single whole-frame call even when they exceed the pair budget."""
    df = _hub_df(300)
    monkeypatch.setenv("GOLDENMATCH_FS_OVERSIZED_CHUNK_PAIRS", "10")
    calls = []

    def score_one(frame):
        calls.append(frame)
        return []

    _score_block_bounded(df, df.height, score_one, tile_above_rows=300)
    assert len(calls) == 1
    assert calls[0] is df


def test_block_under_budget_is_one_untouched_call(monkeypatch) -> None:
    df = _hub_df(300)
    monkeypatch.setenv("GOLDENMATCH_FS_OVERSIZED_CHUNK_PAIRS", "1000000")
    calls = []

    def score_one(frame):
        calls.append(frame)
        return []

    _score_block_bounded(df, df.height, score_one, tile_above_rows=10)
    assert len(calls) == 1
    assert calls[0] is df


def test_zero_budget_disables_tiling(monkeypatch) -> None:
    """The parity escape hatch: 0 restores the pre-#2417 score-whole call."""
    df = _hub_df(300)
    monkeypatch.setenv("GOLDENMATCH_FS_OVERSIZED_CHUNK_PAIRS", "0")
    assert _fs_oversized_pair_budget() == 0
    calls = []

    def score_one(frame):
        calls.append(frame)
        return []

    _score_block_bounded(df, df.height, score_one, tile_above_rows=10)
    assert len(calls) == 1
    assert calls[0] is df


def test_budget_default_and_bad_value(monkeypatch) -> None:
    monkeypatch.delenv("GOLDENMATCH_FS_OVERSIZED_CHUNK_PAIRS", raising=False)
    assert _fs_oversized_pair_budget() == 4_000_000
    monkeypatch.setenv("GOLDENMATCH_FS_OVERSIZED_CHUNK_PAIRS", "not-a-number")
    assert _fs_oversized_pair_budget() == 4_000_000
    monkeypatch.setenv("GOLDENMATCH_FS_OVERSIZED_CHUNK_PAIRS", "-5")
    assert _fs_oversized_pair_budget() == 0


# ── 4. per-call pair count is bounded end-to-end ─────────────────────────────


def test_no_scoring_call_carries_the_whole_block(monkeypatch) -> None:
    df = _hub_df(400)
    monkeypatch.setenv("GOLDENMATCH_FS_OVERSIZED_CHUNK_PAIRS", "2000")
    seen_rows = []

    def score_one(frame):
        seen_rows.append(len(frame))
        return []

    _score_block_bounded(df, df.height, score_one, tile_above_rows=100)
    assert seen_rows, "nothing was scored"
    assert max(_pair_count(r) for r in seen_rows) <= 2000
    assert max(seen_rows) < df.height


# ── 5. end-to-end through score_buckets ──────────────────────────────────────


@pytest.mark.parametrize("bucket_native", ["1", "0"])
def test_score_buckets_hub_block_tiled_matches_whole(monkeypatch, bucket_native) -> None:
    """A hub block that ``_auto_split_block`` cannot split scores to the SAME
    pairs whether it is tiled or handed over whole, on both bucket lanes."""
    df = _hub_df(400)
    mk = _mk()
    blocking = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["zip"])],
        max_block_size=100,
        skip_oversized=False,
    )
    em = train_em(df, mk, n_sample_pairs=500)

    from goldenmatch.backends.score_buckets import score_buckets

    monkeypatch.setenv("GOLDENMATCH_FS_BUCKET_NATIVE", bucket_native)

    monkeypatch.setenv("GOLDENMATCH_FS_OVERSIZED_CHUNK_PAIRS", "0")
    whole = score_buckets(df, blocking, mk, set(), em_result=em)

    monkeypatch.setenv("GOLDENMATCH_FS_OVERSIZED_CHUNK_PAIRS", "2000")
    tiled = score_buckets(df, blocking, mk, set(), em_result=em)

    assert whole, "the hub block produced no pairs — fixture is not exercising the path"
    assert _pairset(tiled) == _pairset(whole)
    assert _scored(tiled) == _scored(whole)
