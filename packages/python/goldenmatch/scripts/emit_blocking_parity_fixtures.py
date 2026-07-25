#!/usr/bin/env python3
"""Emit cross-language parity goldens for the `lsh` + `perceptual` blocking
strategies (Part C of the cross-runtime kernel-closure arc).

Writes tests/parity/fixtures/blocking-strategies.json: for each strategy, the
input rows + resolved config + the produced BLOCKS as a sorted list of
sorted-id-lists. The Python reference (`build_lsh_blocks` /
`build_perceptual_blocks`) is byte-parity with the TS port (`buildLshBlocks` /
`buildPerceptualBlocks`) because the underlying bucketing runs the SAME kernel
(`sketch-core`/`sketch.ts` for lsh; the pure banded-hamming split for perceptual),
so identical (rows, config) -> identical block membership.

Blocks are compared by the `id` column (value-based, order-independent) so the
fixture is position-agnostic. Deterministic; imports goldenmatch + polars. Run:
    POLARS_SKIP_CPU_CHECK=1 .venv/bin/python \
        packages/python/goldenmatch/scripts/emit_blocking_parity_fixtures.py
"""
import json
from pathlib import Path

import polars as pl

from goldenmatch.config.schemas import (
    BlockingConfig,
    LSHKeyConfig,
    PerceptualKeyConfig,
)
from goldenmatch.core.lsh_blocker import build_lsh_blocks
from goldenmatch.core.perceptual_blocker import build_perceptual_blocks

OUT = (
    Path(__file__).resolve().parents[3]
    / "typescript/goldenmatch/tests/parity/fixtures/blocking-strategies.json"
)


def _blocks_by_id(block_results, rows: list[dict]) -> list[list[str]]:
    """Each BlockResult -> the sorted `id`s of its members; blocks sorted."""
    out: list[list[str]] = []
    for br in block_results:
        ids = sorted(br.df.collect()["id"].to_list())
        out.append(ids)
    out.sort()
    return out


# lsh: near-duplicate lexical blocking over the `text` column. Rows chosen so a
# couple of near-dup strings collide into a shared MinHash band while unrelated
# strings don't (num_bands high enough for recall on the tiny set).
LSH_ROWS = [
    {"id": "a", "text": "acme corporation"},
    {"id": "b", "text": "acme corporaton"},   # 1-char typo of a
    {"id": "c", "text": "acme corp"},
    {"id": "d", "text": "globex industries"},
    {"id": "e", "text": "globex industrie"},   # near-dup of d
    {"id": "f", "text": ""},                    # empty -> blocks on nothing
]
LSH_CFG = {"column": "text", "mode": "char", "k": 3, "num_perms": 64, "seed": 0, "num_bands": 32}

# perceptual: banded-hamming LSH over hex 64-bit hashes. Two pairs within a small
# hamming radius share a band; unrelated hashes don't.
PERCEPTUAL_ROWS = [
    {"id": "p", "hash": "ffffffffffffffff"},
    {"id": "q", "hash": "fffffffffffffffe"},   # 1 bit off p
    {"id": "r", "hash": "0000000000000000"},
    {"id": "s", "hash": "0000000000000001"},   # 1 bit off r
    {"id": "t", "hash": "123456789abcdef0"},
    {"id": "u", "hash": None},                   # null -> blocks on nothing
]
PERCEPTUAL_CFG = {"column": "hash", "num_bands": 16, "hash_bits": 64}


def _emit_lsh() -> dict:
    df = pl.DataFrame(LSH_ROWS)
    cfg = BlockingConfig(strategy="lsh", lsh=LSHKeyConfig(**LSH_CFG))
    blocks = _blocks_by_id(build_lsh_blocks(df.lazy(), cfg), LSH_ROWS)
    return {"rows": LSH_ROWS, "config": LSH_CFG, "blocks": blocks}


def _emit_perceptual() -> dict:
    df = pl.DataFrame(PERCEPTUAL_ROWS)
    cfg = BlockingConfig(strategy="perceptual", perceptual=PerceptualKeyConfig(**PERCEPTUAL_CFG))
    blocks = _blocks_by_id(build_perceptual_blocks(df.lazy(), cfg), PERCEPTUAL_ROWS)
    return {"rows": PERCEPTUAL_ROWS, "config": PERCEPTUAL_CFG, "blocks": blocks}


payload = {"lsh": _emit_lsh(), "perceptual": _emit_perceptual()}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"wrote lsh({len(payload['lsh']['blocks'])} blocks) + "
      f"perceptual({len(payload['perceptual']['blocks'])} blocks) -> {OUT}")
