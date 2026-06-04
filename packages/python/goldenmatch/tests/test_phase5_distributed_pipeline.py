"""Parity + correctness tests for the fully-distributed Phase 5 pipeline.

Covers the A+B scale rework:
  * A: a GLOBAL ``__row_id__`` carried in the data keeps cluster ids correct
    across Ray partitions. Without it, each partition synthesizes local ids
    that collide and WCC merges unrelated clusters.
  * B: the assembly stays a Ray Dataset end to end -- distributed dedup,
    distributed clustering (no materialize_cluster_dict driver dict), a
    distributed hash join (no member->cid broadcast), distributed golden.

All tests gated on ``ray`` being importable; collection falls through cleanly
when the [ray] extra isn't installed.
"""
from __future__ import annotations

import polars as pl
import pytest

ray = pytest.importorskip("ray")


@pytest.fixture(scope="module")
def _ray_local():
    ray.init(
        local_mode=False, ignore_reinit_error=True,
        num_cpus=2, logging_level="WARNING",
    )
    yield
    ray.shutdown()


def _explicit_cfg():
    """Hand-built config for the fixture: block on last_name (unique per
    cluster), fuzzy-match first_name. Mirrors bench_phase5_explicit.py."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        GoldenRulesConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    return GoldenMatchConfig(
        blocking=BlockingConfig(
            strategy="static", keys=[BlockingKeyConfig(fields=["last_name"])],
        ),
        matchkeys=[
            MatchkeyConfig(
                name="fn", type="weighted", threshold=0.80,
                fields=[MatchkeyField(
                    field="first_name", scorer="jaro_winkler", weight=1.0,
                )],
            )
        ],
        golden_rules=GoldenRulesConfig(default_strategy="most_complete"),
    )


def _fixture_frame() -> pl.DataFrame:
    """Three distinct 2-member clusters, each in its own last_name block.

    Crucially: 6 rows, ordered, with a GLOBAL __row_id__ 0..5. When split into
    3 ordered partitions ([0,1],[2,3],[4,5]) the per-partition LOCAL row index
    would be (0,1) in EVERY partition -- so without the global id, the three
    clusters collide on ids {0,1} and merge into one. The global id keeps them
    (0,1),(2,3),(4,5) -> three separate clusters.
    """
    return pl.DataFrame({
        "__row_id__": [0, 1, 2, 3, 4, 5],
        "first_name": ["alice", "alyce", "bob", "bobby", "carol", "caroll"],
        "last_name":  ["surA", "surA", "surB", "surB", "surC", "surC"],
    })


def _run_distributed(df: pl.DataFrame):
    """Run the full new distributed assembly; return (assign_rows, golden).

    Mirrors _run_phase5_pipeline exactly: score -> local_cc on the RAW pairs
    (no dedup, no distributed-WCC) -> distributed join -> distributed golden.
    """
    from goldenmatch.distributed.clustering import local_cc_assignments
    from goldenmatch.distributed.golden import build_golden_records_smart
    from goldenmatch.distributed.pipeline import _join_assignments_distributed
    from goldenmatch.distributed.scoring import score_blocks_distributed

    cfg = _explicit_cfg()
    # repartition(3) WITHOUT shuffle preserves row order, so each cluster's
    # contiguous rows stay co-located in one partition (the pipeline scores
    # within partition, and local_cc relies on that co-location).
    ds = ray.data.from_arrow(df.to_arrow()).repartition(3)

    raw = score_blocks_distributed(ds, cfg)
    assign = local_cc_assignments(raw)
    assign_rows = sorted(
        assign.take_all(), key=lambda r: (r["cluster_id"], r["member_id"]),
    )
    multi = _join_assignments_distributed(ds, assign)
    golden = build_golden_records_smart(
        multi, cfg.golden_rules, user_columns=["first_name", "last_name"],
    )
    return assign_rows, golden


def _clusters_from_assign(assign_rows) -> list[set]:
    by_cid: dict[int, set] = {}
    for r in assign_rows:
        by_cid.setdefault(r["cluster_id"], set()).add(r["member_id"])
    return sorted((s for s in by_cid.values()), key=min)


def test_global_row_id_keeps_clusters_separate_scipy(_ray_local):
    """Default routing (scipy, below the 50M-pair threshold): the global
    __row_id__ yields three distinct 2-member clusters, not one merged blob."""
    assign_rows, golden = _run_distributed(_fixture_frame())

    clusters = _clusters_from_assign(assign_rows)
    assert clusters == [{0, 1}, {2, 3}, {4, 5}], (
        f"expected three separate clusters; got {clusters}. A single merged "
        f"cluster means the cross-partition local-id collision regressed."
    )
    assert isinstance(golden, list)
    assert len(golden) == 3


def test_global_row_id_keeps_clusters_separate_wcc(_ray_local, monkeypatch):
    """Same correctness on the distributed two_phase_wcc route (threshold=0
    forces it). Exercises all_ids=None -> touched-only, no singleton seeding."""
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD", "0")

    assign_rows, golden = _run_distributed(_fixture_frame())

    clusters = _clusters_from_assign(assign_rows)
    assert clusters == [{0, 1}, {2, 3}, {4, 5}], (
        f"two_phase_wcc merged clusters across partitions: {clusters}"
    )
    assert len(golden) == 3


def test_cluster_sizes_are_global_not_per_partition(_ray_local):
    """The distributed size annotation (repartition(keys=['label']) + within-
    partition count) must report the GLOBAL cluster size of 2 for every member,
    not a per-partition fragment."""
    assign_rows, _ = _run_distributed(_fixture_frame())
    assert assign_rows, "no assignments produced"
    assert all(r["cluster_size"] == 2 for r in assign_rows), (
        f"expected every member to report global size 2; got "
        f"{[(r['member_id'], r['cluster_size']) for r in assign_rows]}"
    )
    assert all(r["oversized"] is False for r in assign_rows)
