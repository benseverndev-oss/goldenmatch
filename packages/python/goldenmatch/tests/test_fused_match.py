"""Increment 3: the fused Arrow-native match entry (goldenmatch.core.fused_match).

Gate tests for `match_fused_ready` (covered boundary) + a parity test of
`run_match_fused_arrow` against an INDEPENDENT brute-force oracle (block by key
with the same null/sentinel drop, score with jaro_winkler, union-find) -- so the
entry's marshaling (scorer id, weight, threshold, column selection, block-key
semantics) is proven correct end to end, not just kernel-vs-kernel.
"""

from __future__ import annotations

from collections import defaultdict

import pyarrow as pa
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core import fused_match
from goldenmatch.core._native_loader import native_module

_HAS_FUSED = fused_match._match_fused_symbol() is not None


def _covered_config(threshold: float = 0.85) -> GoldenMatchConfig:
    return GoldenMatchConfig(
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["blk"])]),
        matchkeys=[
            MatchkeyConfig(
                name="mk",
                type="weighted",
                fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
                threshold=threshold,
            )
        ],
    )


# ---- gate ---------------------------------------------------------------

def test_ready_true_on_covered_config():
    assert fused_match.match_fused_ready(_covered_config()) is True


def test_ready_false_on_key_transform():
    c = _covered_config()
    c.blocking.keys[0].transforms = ["lowercase"]
    assert fused_match.match_fused_ready(c) is False


def test_ready_false_on_field_transform():
    c = _covered_config()
    c.matchkeys[0].fields[0].transforms = ["strip"]
    assert fused_match.match_fused_ready(c) is False


def test_ready_false_on_uncovered_scorer():
    c = _covered_config()
    c.matchkeys[0].fields[0].scorer = "soundex_match"
    assert fused_match.match_fused_ready(c) is False


def test_ready_false_on_multi_pass_blocking():
    c = _covered_config()
    c.blocking.strategy = "multi_pass"
    assert fused_match.match_fused_ready(c) is False


def test_ready_false_on_two_blocking_keys():
    c = _covered_config()
    c.blocking.keys.append(BlockingKeyConfig(fields=["name"]))
    assert fused_match.match_fused_ready(c) is False


def test_ready_false_on_missing_threshold():
    c = _covered_config()
    c.matchkeys[0].threshold = None
    assert fused_match.match_fused_ready(c) is False


# ---- parity vs an independent brute oracle -----------------------------

def _brute_clusters(keys, names, threshold):
    jw = native_module().jaro_winkler_similarity
    blocks: dict[str, list[int]] = defaultdict(list)
    for i, k in enumerate(keys):
        if k is None:
            continue
        if str(k).strip().lower() in ("nan", "null", "none"):
            continue
        blocks[str(k)].append(i)

    parent = list(range(len(keys)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for members in blocks.values():
        for ai in range(len(members)):
            for bi in range(ai + 1, len(members)):
                a, b = members[ai], members[bi]
                if jw(names[a], names[b]) >= threshold:
                    ra, rb = find(a), find(b)
                    if ra != rb:
                        parent[ra] = rb

    comps: dict[int, list[int]] = defaultdict(list)
    for i in range(len(keys)):
        comps[find(i)].append(i)
    return {frozenset(v) for v in comps.values() if len(v) >= 2}


def _table_to_clusters(tbl):
    comps: dict[int, list[int]] = defaultdict(list)
    for r, c in zip(tbl.column("__row_id__").to_pylist(), tbl.column("__cluster_id__").to_pylist()):
        comps[c].append(r)
    return {frozenset(v) for v in comps.values() if len(v) >= 2}


@pytest.mark.skipif(not _HAS_FUSED, reason="goldenmatch-native match_fused not built")
def test_run_match_fused_arrow_matches_brute_oracle():
    # Blocks form on `blk`; near-duplicate names cross the jaro_winkler threshold.
    keys = ["a", "a", "a", "b", "b", "c", None, "NULL", "nan", "d", "d"]
    names = [
        "jonathan", "jonathon", "michael",   # blk a: jonathan~jonathon merge, michael alone
        "sarah", "sarah",                    # blk b: exact merge
        "lone",                              # blk c: singleton
        "dropme1", "dropme2", "dropme3",     # null / NULL / nan keys dropped
        "kevin", "kevni",                    # blk d: near-dup merge
    ]
    config = _covered_config(threshold=0.85)
    columns = {"blk": pa.array(keys), "name": pa.array(names)}

    tbl = fused_match.run_match_fused_arrow(columns, config)
    assert tbl is not None
    got = _table_to_clusters(tbl)
    want = _brute_clusters(keys, names, 0.85)
    assert got == want


def test_run_match_fused_arrow_declines_uncovered():
    c = _covered_config()
    c.matchkeys[0].fields[0].scorer = "soundex_match"
    assert fused_match.run_match_fused_arrow({"blk": pa.array(["a"]), "name": pa.array(["x"])}, c) is None
