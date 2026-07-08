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


def test_ready_true_with_key_transform():
    # Transforms are covered — derived host-side via the pipeline reference.
    c = _covered_config()
    c.blocking.keys[0].transforms = ["lowercase", "soundex"]
    assert fused_match.match_fused_ready(c) is True


def test_ready_true_with_field_transform():
    c = _covered_config()
    c.matchkeys[0].fields[0].transforms = ["lowercase", "strip"]
    assert fused_match.match_fused_ready(c) is True


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

def _brute_clusters(keys, names, threshold, key_transforms=(), score_transforms=()):
    """Independent oracle. Applies the SAME transforms via `apply_transforms`
    (the per-value reference `_build_block_key_expr`/`_get_transformed_values`
    fall back to), then blocks + scores + union-finds."""
    from goldenmatch.utils.transforms import apply_transforms

    jw = native_module().jaro_winkler_similarity

    def _xf(v, chain):
        return apply_transforms(v, list(chain)) if chain else v

    keys = [_xf(k, key_transforms) for k in keys]
    names = [_xf(nm, score_transforms) for nm in names]

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
                if names[a] is None or names[b] is None:
                    continue
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


@pytest.mark.skipif(not _HAS_FUSED, reason="goldenmatch-native match_fused not built")
def test_run_match_fused_arrow_matches_brute_oracle_with_transforms():
    # Block key normalized by lowercase+strip (case/whitespace noise collapses into
    # one block); score field normalized the same. Proves the host-side transform
    # derivation is byte-faithful to the pipeline reference.
    keys = [" Smith ", "smith", "SMITH", "jones", "Jones ", "lee"]
    names = ["Jonathan", "jonathon ", " JONATHAN", "sarah", "SARAH", "solo"]
    config = _covered_config(threshold=0.85)
    config.blocking.keys[0].transforms = ["lowercase", "strip"]
    config.matchkeys[0].fields[0].transforms = ["lowercase", "strip"]
    columns = {"blk": pa.array(keys), "name": pa.array(names)}

    tbl = fused_match.run_match_fused_arrow(columns, config)
    assert tbl is not None
    got = _table_to_clusters(tbl)
    want = _brute_clusters(
        keys, names, 0.85, key_transforms=["lowercase", "strip"], score_transforms=["lowercase", "strip"]
    )
    assert got == want


@pytest.mark.skipif(not _HAS_FUSED, reason="goldenmatch-native match_fused not built")
def test_run_match_fused_arrow_soundex_block_key():
    # soundex block key exercises the map_elements(apply_transforms) fallback path
    # end to end (the common auto-config blocking transform).
    keys = ["Smith", "Smyth", "Smithe", "Jones", "Jonez", "Zzzz"]
    names = ["robert", "robbert", "roberto", "alice", "alicia", "solo"]
    config = _covered_config(threshold=0.80)
    config.blocking.keys[0].transforms = ["lowercase", "soundex"]
    columns = {"blk": pa.array(keys), "name": pa.array(names)}

    tbl = fused_match.run_match_fused_arrow(columns, config)
    assert tbl is not None
    got = _table_to_clusters(tbl)
    want = _brute_clusters(keys, names, 0.80, key_transforms=["lowercase", "soundex"])
    assert got == want


def test_run_match_fused_arrow_declines_uncovered():
    c = _covered_config()
    c.matchkeys[0].fields[0].scorer = "soundex_match"
    assert fused_match.run_match_fused_arrow({"blk": pa.array(["a"]), "name": pa.array(["x"])}, c) is None


# ---- Fellegi-Sunter (probabilistic) fused path -------------------------

def _probabilistic_config():
    return GoldenMatchConfig(
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["blk"])]),
        matchkeys=[
            MatchkeyConfig(
                name="mk",
                type="probabilistic",
                link_threshold=0.5,
                fields=[
                    MatchkeyField(field="name", scorer="jaro_winkler", levels=3, partial_threshold=0.8)
                ],
            )
        ],
    )


def _em():
    from goldenmatch.core.probabilistic import EMResult

    # match_weights per level 0/1/2 (log2(m/u)); the only field that matters here.
    return EMResult(
        m_probs={"name": [0.1, 0.3, 0.6]},
        u_probs={"name": [0.7, 0.2, 0.1]},
        match_weights={"name": [-2.0, 0.585, 2.585]},
        converged=True,
        iterations=1,
        proportion_matched=0.1,
    )


def test_fs_ready_true_on_probabilistic_false_on_weighted():
    assert fused_match.match_fused_fs_ready(_probabilistic_config()) is True
    assert fused_match.match_fused_fs_ready(_covered_config()) is False  # weighted


@pytest.mark.skipif(not _HAS_FUSED, reason="goldenmatch-native match_fused_fs not built")
def test_match_fused_fs_matches_pipeline_fs_block_scorer():
    import polars as pl
    from goldenmatch.core.blocker import build_blocks
    from goldenmatch.core.probabilistic import probabilistic_block_scorer

    config = _probabilistic_config()
    em = _em()
    keys = ["a", "a", "a", "b", "b", "c"]
    names = ["jonathan", "jonathon", "michael", "sarah", "sara", "solo"]
    df = (
        pl.DataFrame({"blk": keys, "name": names})
        .with_row_index("__row_id__")
        .with_columns(pl.col("__row_id__").cast(pl.Int64))
    )

    # fused FS
    columns = {"blk": pa.array(keys), "name": pa.array(names)}
    tbl = fused_match.run_match_fused_fs_arrow(columns, config, em)
    assert tbl is not None
    got = _table_to_clusters(tbl)

    # reference: the pipeline FS block path (same em -> same kernel FS math)
    scorer = probabilistic_block_scorer(config.get_matchkeys()[0], em)
    pairs = []
    for br in build_blocks(df.lazy(), config.blocking):
        g = br.df.collect() if hasattr(br.df, "collect") else br.df
        pairs += scorer(g)
    parent = list(range(df.height))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, _s in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    comps = defaultdict(list)
    for i in range(df.height):
        comps[find(i)].append(i)
    want = {frozenset(v) for v in comps.values() if len(v) >= 2}
    assert got == want


# ---- multi-pass blocking fused path ------------------------------------

def _multipass_config():
    return GoldenMatchConfig(
        blocking=BlockingConfig(
            strategy="multi_pass",
            keys=[BlockingKeyConfig(fields=["blk1"])],
            passes=[BlockingKeyConfig(fields=["blk1"]), BlockingKeyConfig(fields=["blk2"])],
        ),
        matchkeys=[
            MatchkeyConfig(
                name="mk",
                type="weighted",
                threshold=0.85,
                fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
            )
        ],
    )


def test_multipass_ready_true_false():
    assert fused_match.match_fused_multipass_ready(_multipass_config()) is True
    assert fused_match.match_fused_multipass_ready(_covered_config()) is False  # static


@pytest.mark.skipif(not _HAS_FUSED, reason="goldenmatch-native match_fused not built")
def test_multipass_merges_across_passes():
    # pass1 blocks on blk1: {0,1}(a), {4,5}(z); pass2 on blk2: {0,2}(p), {3,4}(r).
    # All names identical -> every intra-block pair merges; the union chains
    # 0-1-2 (0-1 via pass1, 0-2 via pass2) and 3-4-5 (3-4 pass2, 4-5 pass1).
    blk1 = ["a", "a", "x", "y", "z", "z"]
    blk2 = ["p", "q", "p", "r", "r", "s"]
    name = ["john"] * 6
    config = _multipass_config()
    columns = {"blk1": pa.array(blk1), "blk2": pa.array(blk2), "name": pa.array(name)}
    tbl = fused_match.run_match_fused_multipass_arrow(columns, config)
    assert tbl is not None
    got = _table_to_clusters(tbl)
    assert got == {frozenset({0, 1, 2}), frozenset({3, 4, 5})}


@pytest.mark.skipif(not _HAS_FUSED, reason="goldenmatch-native match_fused not built")
def test_multipass_equals_single_pass_when_one_pass():
    # A one-pass multi_pass config must equal the single-key fused result.
    blk = ["a", "a", "a", "b", "b", "c"]
    name = ["jonathan", "jonathon", "michael", "sarah", "sarah", "lone"]
    columns = {"blk": pa.array(blk), "name": pa.array(name)}

    single = _covered_config(threshold=0.85)
    single.blocking.keys = [BlockingKeyConfig(fields=["blk"])]
    mp = GoldenMatchConfig(
        blocking=BlockingConfig(
            strategy="multi_pass",
            keys=[BlockingKeyConfig(fields=["blk"])],
            passes=[BlockingKeyConfig(fields=["blk"])],
        ),
        matchkeys=single.matchkeys,
    )
    got_single = _table_to_clusters(fused_match.run_match_fused_arrow(columns, single))
    got_mp = _table_to_clusters(fused_match.run_match_fused_multipass_arrow(columns, mp))
    assert got_mp == got_single
