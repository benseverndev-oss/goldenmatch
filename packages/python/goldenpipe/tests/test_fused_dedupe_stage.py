"""Increment 4: the opt-in Arrow-native FusedDedupeStage (GoldenPipe integration).

Verifies the stage runs the fused match kernel over the frame's columns as Arrow
and emits `clusters` + `cluster_assignments`, that it declines an uncovered config
LOUDLY (never a silent fallback), and that its clusters match an independent
brute-force oracle.
"""

from __future__ import annotations

from collections import defaultdict

import polars as pl
import pytest

pytest.importorskip("goldenmatch")

from goldenmatch.core.fused_match import _match_fused_symbol  # noqa: E402
from goldenpipe.adapters.match import FusedDedupeStage  # noqa: E402
from goldenpipe.models.context import PipeContext, StageStatus  # noqa: E402

_HAS_FUSED = _match_fused_symbol() is not None

_COVERED_CFG = {
    "blocking": {"strategy": "static", "keys": [{"fields": ["blk"]}]},
    "matchkeys": [
        {
            "name": "mk",
            "type": "weighted",
            "threshold": 0.85,
            "fields": [{"field": "name", "scorer": "jaro_winkler", "weight": 1.0}],
        }
    ],
}


def _frame():
    keys = ["a", "a", "a", "b", "b", "c"]
    names = ["jonathan", "jonathon", "michael", "sarah", "sarah", "lone"]
    return pl.DataFrame({"blk": keys, "name": names})


def _brute(df: pl.DataFrame, threshold: float):
    from goldenmatch.core._native_loader import native_module

    jw = native_module().jaro_winkler_similarity
    keys = df["blk"].to_list()
    names = df["name"].to_list()
    blocks: dict[str, list[int]] = defaultdict(list)
    for i, k in enumerate(keys):
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


def test_validate_declines_without_config():
    ctx = PipeContext(df=_frame())
    with pytest.raises(RuntimeError, match="explicit covered config"):
        FusedDedupeStage().validate(ctx)


def test_validate_declines_uncovered_config():
    cfg = {
        "blocking": {"strategy": "static", "keys": [{"fields": ["blk"]}]},
        "matchkeys": [
            {
                "name": "mk",
                "type": "weighted",
                "threshold": 0.85,
                "fields": [{"field": "name", "scorer": "soundex_match", "weight": 1.0}],
            }
        ],
    }
    ctx = PipeContext(df=_frame(), stage_config=cfg)
    with pytest.raises(RuntimeError, match="not covered"):
        FusedDedupeStage().validate(ctx)


@pytest.mark.skipif(not _HAS_FUSED, reason="goldenmatch-native match_fused not built")
def test_run_emits_clusters_matching_brute_oracle():
    df = _frame()
    ctx = PipeContext(df=df, stage_config=_COVERED_CFG)
    stage = FusedDedupeStage()
    stage.validate(ctx)
    res = stage.run(ctx)

    assert res.status == StageStatus.SUCCESS
    assert "cluster_assignments" in ctx.artifacts
    assert ctx.artifacts["cluster_assignments"].num_rows == df.height  # one row per record

    got = {frozenset(m) for m in ctx.artifacts["clusters"].values()}
    assert got == _brute(df, 0.85)
