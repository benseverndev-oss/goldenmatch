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


def _mixed_person_frame():
    import polars as pl
    rows = []
    rid = 0
    for surname, n in [("smith", 6), ("jones", 5), ("lee", 4)]:
        for k in range(n):
            rows.append({"__row_id__": rid, "first": f"john{k%2}", "last": surname})
            rid += 1
    for i in range(40):  # singletons
        rows.append({"__row_id__": rid, "first": f"uniq{i}", "last": f"sur{i}"})
        rid += 1
    return pl.DataFrame(rows)


def _blocks_and_mk(df):
    from goldenmatch.core.blocker import build_blocks
    from goldenmatch.config.schemas import (
        BlockingConfig, BlockingKeyConfig, MatchkeyConfig, MatchkeyField,
    )
    blocking = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["last"], transforms=["lowercase"])],
    )
    blocks = build_blocks(df.lazy(), blocking)
    mk = MatchkeyConfig(
        name="mk", type="weighted", threshold=0.7,
        fields=[MatchkeyField(field="first", scorer="jaro_winkler", weight=1.0)],
    )
    return blocks, mk


def _per_block_reference(blocks, mk):
    """Ground truth = today's behavior: score each block directly, no batching."""
    from goldenmatch.core.scorer import _score_one_block
    out = []
    for b in blocks:
        out.extend(_score_one_block(b, mk, set(), across_files_only=False,
                                    source_lookup=None))
    return out


def test_batched_equals_per_block(monkeypatch):
    from goldenmatch.core import scorer
    df = _mixed_person_frame()
    blocks, mk = _blocks_and_mk(df)

    ref = _per_block_reference(blocks, mk)

    monkeypatch.setattr(scorer, "_SOLO_BLOCK_MIN_PAIRS", 10_000)
    monkeypatch.setattr(scorer, "_BATCH_BINS_PER_WORKER", 4)
    got = scorer.score_blocks_parallel(list(blocks), mk, set(), max_workers=4)

    norm = lambda ps: sorted((min(a, b), max(a, b), round(s, 6)) for a, b, s in ps)
    assert norm(got) == norm(ref)
    assert got, "sanity: the smith/jones/lee blocks should yield some pairs"


def test_batched_equals_per_block_solo_path(monkeypatch):
    """Force the multi-row blocks through the SOLO branch (batch of 1) and
    confirm byte-identity still holds -- covers _score_block_batch's single-block
    path, the 'big block gets its own future' case."""
    from goldenmatch.core import scorer
    df = _mixed_person_frame()
    blocks, mk = _blocks_and_mk(df)

    ref = _per_block_reference(blocks, mk)

    # Threshold of 1 pair -> every block with >=2 rows goes solo; singletons
    # (0 pairs) still bin. Exercises both the solo and small branches.
    monkeypatch.setattr(scorer, "_SOLO_BLOCK_MIN_PAIRS", 1)
    got = scorer.score_blocks_parallel(list(blocks), mk, set(), max_workers=4)

    norm = lambda ps: sorted((min(a, b), max(a, b), round(s, 6)) for a, b, s in ps)
    assert norm(got) == norm(ref)
    assert got, "sanity: grouped blocks should yield pairs on the solo path too"
