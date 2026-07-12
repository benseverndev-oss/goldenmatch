"""Unit tests for the scorer's block-batch planner (adaptive block-batching)."""
from dataclasses import dataclass


@dataclass
class _FakeBlock:
    """Stand-in for BlockResult -- the planner only reads .n_rows and identity."""
    block_key: str
    n_rows: int | None


def _pairs(n):
    return n * (n - 1) // 2


def test_empty_blocks_empty_plan():
    from goldenmatch.core.scorer import _plan_block_batches
    assert _plan_block_batches([], max_workers=4) == []


def test_big_blocks_go_solo(monkeypatch):
    from goldenmatch.core import scorer
    monkeypatch.setattr(scorer, "_SOLO_BLOCK_MIN_PAIRS", 1000)
    big = _FakeBlock("big", n_rows=100)      # 4950 pairs >= 1000 -> solo
    small = _FakeBlock("small", n_rows=3)    # 3 pairs -> binned
    batches = scorer._plan_block_batches([big, small], max_workers=4)
    solo = [b for b in batches if len(b) == 1 and b[0].block_key == "big"]
    assert len(solo) == 1, "big block must be its own batch"


def test_small_blocks_bin_into_bounded_count(monkeypatch):
    from goldenmatch.core import scorer
    monkeypatch.setattr(scorer, "_SOLO_BLOCK_MIN_PAIRS", 10_000)
    monkeypatch.setattr(scorer, "_BATCH_BINS_PER_WORKER", 4)
    blocks = [_FakeBlock(f"b{i}", n_rows=2) for i in range(1000)]  # all tiny
    batches = scorer._plan_block_batches(blocks, max_workers=8)
    # bounded by max_workers * K = 32 bins
    assert len(batches) <= 32
    # every block appears exactly once
    seen = [blk.block_key for batch in batches for blk in batch]
    assert sorted(seen) == sorted(b.block_key for b in blocks)


def test_none_n_rows_round_robin_still_batches(monkeypatch):
    from goldenmatch.core import scorer
    monkeypatch.setattr(scorer, "_BATCH_BINS_PER_WORKER", 4)
    blocks = [_FakeBlock(f"b{i}", n_rows=None) for i in range(500)]
    batches = scorer._plan_block_batches(blocks, max_workers=8)
    assert 0 < len(batches) <= 32
    seen = [blk.block_key for batch in batches for blk in batch]
    assert sorted(seen) == sorted(b.block_key for b in blocks)


def test_every_block_scored_exactly_once_mixed(monkeypatch):
    from goldenmatch.core import scorer
    monkeypatch.setattr(scorer, "_SOLO_BLOCK_MIN_PAIRS", 1000)
    blocks = (
        [_FakeBlock(f"big{i}", n_rows=200) for i in range(3)]     # solo
        + [_FakeBlock(f"sm{i}", n_rows=2) for i in range(100)]    # binned
        + [_FakeBlock(f"nn{i}", n_rows=None) for i in range(10)]  # round-robin
    )
    batches = scorer._plan_block_batches(blocks, max_workers=4)
    seen = [blk.block_key for batch in batches for blk in batch]
    assert sorted(seen) == sorted(b.block_key for b in blocks)
    assert len(seen) == len(blocks), "no block duplicated or dropped"
