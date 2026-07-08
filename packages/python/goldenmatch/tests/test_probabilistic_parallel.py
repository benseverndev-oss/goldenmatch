"""Parallel Fellegi-Sunter block scoring must be output-identical to sequential.

``score_probabilistic_blocks_batched`` scores its scoring units (blocks for the
native/scalar path, row-capped batches for the vectorized path) across a thread
pool; the FS kernels release the GIL so this is real parallelism. Because each
unit is scored against a frozen exclude snapshot and cross-unit duplicate pairs
are deduped by canonical key, the emitted pair set — and therefore the clusters —
must match the sequential (``GOLDENMATCH_FS_WORKERS=1``) path exactly.
"""
import os

import polars as pl
import pytest

import goldenmatch as gm
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.blocker import build_blocks
from goldenmatch.core.probabilistic import (
    load_or_train_em,
    score_probabilistic_blocks_batched,
)


def _config() -> GoldenMatchConfig:
    # Union blocking (name-prefix OR zip) deliberately surfaces some pairs in
    # more than one block, exercising the cross-unit dedup path.
    blk = BlockingConfig(
        strategy="static",
        union_mode=True,
        keys=[
            BlockingKeyConfig(fields=["name"], transforms=["uppercase", "strip_all", "substring:0:4"]),
            BlockingKeyConfig(fields=["zip"], transforms=["strip"]),
        ],
    )
    mk = MatchkeyConfig(
        name="fs",
        type="probabilistic",
        threshold=0.9,
        fields=[
            MatchkeyField(field="name", scorer="jaro_winkler", levels=3, partial_threshold=0.85),
            MatchkeyField(field="city", scorer="jaro_winkler", levels=2, partial_threshold=0.9),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ],
    )
    return GoldenMatchConfig(matchkeys=[mk], blocking=blk)


def _frame() -> pl.DataFrame:
    seeds = [
        ("ACME WIDGETS INC", "SPRINGFIELD", "10001"),
        ("ACME WIDGETS, INC.", "SPRINGFIELD", "10001"),
        ("ACME WIDGET INC", "SPRINGFIELD", "10001"),
        ("BETA HOLDINGS LLC", "SPRINGFIELD", "10001"),
        ("BETA HOLDING LLC", "SPRINGFIELD", "10001"),
        ("ACME LOGISTICS CO", "PORTLAND", "97201"),
        ("ACME LOGISTIC COMPANY", "PORTLAND", "97201"),
        ("GAMMA TRUST", "PORTLAND", "97201"),
        ("DELTA BANK NA", "AUSTIN", "78701"),
        ("DELTA BANK, N.A.", "AUSTIN", "78701"),
    ]
    rows = [dict(zip(("name", "city", "zip"), seeds[i % len(seeds)])) for i in range(30)]
    return pl.DataFrame(rows).with_row_index("tuple_id")


def _fs_workers(monkeypatch, n):
    monkeypatch.setenv("GOLDENMATCH_FS_WORKERS", str(n))


def _clusters(df, cfg, workers, monkeypatch):
    _fs_workers(monkeypatch, workers)
    res = gm.dedupe_df(df, config=cfg)
    return sorted(tuple(sorted(c["members"])) for c in res.clusters.values())


def test_parallel_clusters_match_sequential(monkeypatch):
    df = _frame()
    cfg = _config()
    seq = _clusters(df, cfg, 1, monkeypatch)
    par = _clusters(df, cfg, 8, monkeypatch)
    assert par == seq
    assert any(len(c) > 1 for c in seq)  # fixture actually merges


def test_batched_pairs_match_sequential(monkeypatch):
    # The block scorer + EM expect the pipeline's internal __row_id__ column.
    df = _frame().with_row_index("__row_id__")
    cfg = _config()
    mk = cfg.matchkeys[0]
    blocks = build_blocks(df.lazy(), cfg.blocking)
    em = load_or_train_em(df, mk, blocks=blocks, blocking_fields=["name", "zip"])

    _fs_workers(monkeypatch, 1)
    seq = score_probabilistic_blocks_batched(blocks, mk, em, set())
    _fs_workers(monkeypatch, 8)
    par = score_probabilistic_blocks_batched(blocks, mk, em, set())

    seq_pairs = {(min(a, b), max(a, b)) for a, b, _s in seq}
    par_pairs = [(min(a, b), max(a, b)) for a, b, _s in par]
    assert set(par_pairs) == seq_pairs
    assert len(par_pairs) == len(set(par_pairs))  # no duplicate emissions


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
