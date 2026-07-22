"""Real-Ray end-to-end integration tests for the FELLEGI-SUNTER (probabilistic)
matchkey type on the Phase-5 distributed pipeline.

Before this file, distributed FS coverage stopped at the per-partition kernel
(`_score_partition_with_config`) and driver model-prep (`_prepare_fs_models`),
both exercised only with a duck-typed fake Dataset -- NOT through real Ray
dispatch, and NEVER through the full `_run_phase5_pipeline` (score -> shuffle ->
distributed WCC -> golden). Every real-Ray distributed test and bench used a
WEIGHTED / exact config; nothing asserted that an FS config survives
`score_blocks_distributed` on a real Ray Dataset and recovers the injected
clusters. This closes that rung between "FS kernel unit-tested with a fake
Dataset" and "100M FS bench".

What each test proves on a REAL local Ray runtime:
  * scoring: `score_blocks_distributed(ds, fs_cfg)` trains ONE EMResult on the
    driver (`_prepare_fs_models`), ships it to workers, and the per-partition
    `score_buckets` FS branch recovers the injected duplicate pairs across
    input-partition boundaries (block-shuffle co-locates on the FS blocking key).
  * e2e (block-shuffle ON): `_run_phase5_pipeline` with an explicit probabilistic
    config runs score -> block-shuffle -> randomized_contraction WCC and the
    persisted cluster assignments recover the injected clusters.
  * e2e (legacy, single partition): the FS kernel + `local_cc_assignments`
    recover the clusters when everything is co-located in one partition.

Gated on `ray` being importable; collection falls through when the [ray] extra
is absent. Self-contained (xdist worker isolation): the synthetic generator and
FS config builder are inline, not imported from scripts/.
"""
from __future__ import annotations

import itertools
import random

import polars as pl
import pytest

ray = pytest.importorskip("ray")


@pytest.fixture(scope="module")
def _ray_local():
    """Module-scoped Ray init so startup is paid once. ignore_reinit_error in
    case an earlier test module already touched ray.

    Two knobs matter for the block-shuffle FS path on a SMALL local cluster:
      * ``num_cpus`` is oversubscribed (6 logical slots on a smaller box). The
        block-shuffle path spawns long-lived ``HashShuffleAggregator`` actors
        that each hold a CPU slot; with too few slots those actors starve the
        ``_score`` op (which reserves ``_SCORE_NUM_CPUS``) and the run deadlocks
        with ~0% CPU. The data here is tiny, so oversubscribing slots only
        relieves scheduling, it does not overcommit real work.
      * ``_SCORE_NUM_CPUS`` is pinned to 1 (its module default of 2 is tuned for
        a 64 GB / many-core distributed box). Saved/restored around the module.
    """
    from goldenmatch.distributed import scoring

    saved_cpus = scoring._SCORE_NUM_CPUS
    scoring._SCORE_NUM_CPUS = 1
    ray.init(
        local_mode=False, ignore_reinit_error=True,
        num_cpus=6, logging_level="WARNING",
    )
    try:
        yield
    finally:
        ray.shutdown()
        scoring._SCORE_NUM_CPUS = saved_cpus


def _gen_fs_data(
    n_base: int = 200, dup_frac: float = 0.2, seed: int = 7,
) -> tuple[pl.DataFrame, set[tuple[int, int]]]:
    """Synthetic person-shape frame with a GLOBAL ``__row_id__`` == row position
    and injected near-duplicates, plus the ground-truth within-entity pair set.

    Each base row is its own entity. A dup copies an ORIGINAL base (no
    dup-of-dup chains) with a transposed first name but the SAME email (an exact
    FS signal) and the SAME zip (so the dup lands in its base's blocking block).
    GT is the full per-entity CLIQUE in ``__row_id__`` space. Names are
    high-entropy (varied prefixes) so genuinely different people are separable.
    """
    rng = random.Random(seed)
    cons, vow = "bcdfghjklmnprstvwz", "aeiou"

    def _name() -> str:
        return "".join(
            rng.choice(cons) + rng.choice(vow) for _ in range(rng.randint(3, 4))
        )

    def _typo(s: str) -> str:
        if len(s) < 4:
            return s
        j = rng.randrange(len(s) - 1)
        return s[:j] + s[j + 1] + s[j] + s[j + 2:]

    n_zip = max(1, n_base // 40)
    rows: list[dict] = []
    entity: list[int] = []
    for i in range(n_base):
        f, l = _name(), _name()
        rows.append({
            "first_name": f, "last_name": l,
            "email": f"{f}.{l}.{i}@example.com",
            "zip": f"{rng.randrange(n_zip):05d}",
        })
        entity.append(i)

    for _ in range(int(n_base * dup_frac)):
        base_idx = rng.randrange(n_base)
        src = rows[base_idx]
        rows.append({
            "first_name": _typo(src["first_name"]),
            "last_name": src["last_name"],
            "email": src["email"],
            "zip": src["zip"],
        })
        entity.append(base_idx)

    groups: dict[int, list[int]] = {}
    for idx, e in enumerate(entity):
        groups.setdefault(e, []).append(idx)
    gt: set[tuple[int, int]] = set()
    for members in groups.values():
        if len(members) >= 2:
            for a, b in itertools.combinations(sorted(members), 2):
                gt.add((a, b))

    df = pl.DataFrame(rows).with_row_index("__row_id__").with_columns(
        pl.col("__row_id__").cast(pl.Int64),
    )
    return df, gt


def _fs_cfg():
    """Explicit Fellegi-Sunter config: one probabilistic matchkey (name +
    exact email) blocked on zip -- the same shape the FS bench uses."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="fs", type="probabilistic", fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3,
                          partial_threshold=0.85),
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=2,
                          partial_threshold=0.85),
            MatchkeyField(field="email", scorer="exact", levels=2),
        ])],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
    )


def _pairs_from_assignments(assignments_dir: str) -> set[tuple[int, int]]:
    """Read the Phase-5 cluster-assignment parquet (Ray writes one part file per
    partition into a directory) and expand each multi-member cluster into its
    canonical (min, max) within-cluster pair set."""
    import glob
    import os

    parts = glob.glob(os.path.join(assignments_dir, "**", "*.parquet"), recursive=True)
    assert parts, f"no assignment parquet written under {assignments_dir}"
    frame = pl.concat([pl.read_parquet(p) for p in parts], how="vertical_relaxed")

    clusters: dict[int, list[int]] = {}
    for row in frame.iter_rows(named=True):
        clusters.setdefault(int(row["cluster_id"]), []).append(int(row["member_id"]))
    pairs: set[tuple[int, int]] = set()
    for members in clusters.values():
        if len(members) >= 2:
            for a, b in itertools.combinations(sorted(members), 2):
                pairs.add((a, b))
    return pairs


def _recall(found: set[tuple[int, int]], gt: set[tuple[int, int]]) -> float:
    if not gt:
        return 1.0
    return len(found & gt) / len(gt)


# ── Test 1: distributed FS scoring recovers the injected pairs ───────

def test_fs_scoring_distributed_recovers_pairs(_ray_local, monkeypatch):
    """`score_blocks_distributed` with a probabilistic config trains one EMResult
    on the driver, ships it to workers, and the per-partition FS scorer recovers
    the injected duplicate pairs -- ACROSS input partitions (block-shuffle ON co-
    locates on the zip blocking key). This is the missing real-Ray FS scoring
    coverage; the existing distributed-scoring test only exercised a weighted
    config."""
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE", "1")
    from goldenmatch.distributed.scoring import score_blocks_distributed

    df, gt = _gen_fs_data()
    ds = ray.data.from_arrow(df.to_arrow()).repartition(3)

    pairs_ds = score_blocks_distributed(ds, _fs_cfg())
    rows = list(pairs_ds.take_all())
    assert rows, "FS distributed scoring produced no pairs"
    assert {"id_a", "id_b", "score"} <= set(rows[0].keys())

    found = {
        (min(int(r["id_a"]), int(r["id_b"])), max(int(r["id_a"]), int(r["id_b"])))
        for r in rows
    }
    recall = _recall(found, gt)
    assert recall >= 0.9, (
        f"FS distributed scoring recall {recall:.3f} < 0.9 "
        f"({len(found & gt)}/{len(gt)} GT pairs recovered)"
    )


# ── Test 2: full Phase-5 e2e (block-shuffle ON) recovers clusters ────

def test_fs_phase5_e2e_recovers_clusters_block_shuffle(
    _ray_local, monkeypatch, tmp_path,
):
    """`_run_phase5_pipeline` with an explicit FS config runs score ->
    block-shuffle -> randomized_contraction WCC end to end; the persisted cluster
    assignments recover the injected clusters. Exercises the recall-complete
    distributed path (block-shuffle co-location + real distributed WCC) with FS
    scores for the first time."""
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE", "1")
    from goldenmatch.distributed.pipeline import _run_phase5_pipeline

    df, gt = _gen_fs_data()
    ds = ray.data.from_arrow(df.to_arrow()).repartition(3)
    assignments_dir = str(tmp_path / "assignments")

    _run_phase5_pipeline(
        ds,
        config=_fs_cfg(),
        confidence_required=False,
        assignments_output_path=assignments_dir,
        _skip_golden=True,
    )

    found = _pairs_from_assignments(assignments_dir)
    recall = _recall(found, gt)
    assert recall >= 0.9, (
        f"FS Phase-5 e2e (block-shuffle) cluster recall {recall:.3f} < 0.9 "
        f"({len(found & gt)}/{len(gt)} GT pairs recovered)"
    )
    # Sanity: the FS scores did not collapse every record into one giant cluster.
    assert len(found) < len(gt) * 20, (
        f"over-merge: {len(found)} recovered pairs vs {len(gt)} GT pairs"
    )


# ── Test 3: Phase-5 e2e legacy path (single partition) ───────────────

def test_fs_phase5_e2e_legacy_single_partition(
    _ray_local, monkeypatch, tmp_path,
):
    """With block-shuffle OFF and a single input partition, everything is
    co-located, so the legacy per-partition FS kernel + `local_cc_assignments`
    must recover the clusters. Proves the FS scoring kernel is correct under real
    Ray worker serialization of the driver-trained EMResult, independent of the
    block-shuffle machinery."""
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE", "0")
    from goldenmatch.distributed.pipeline import _run_phase5_pipeline

    df, gt = _gen_fs_data()
    ds = ray.data.from_arrow(df.to_arrow()).repartition(1)
    assignments_dir = str(tmp_path / "assignments_legacy")

    _run_phase5_pipeline(
        ds,
        config=_fs_cfg(),
        confidence_required=False,
        assignments_output_path=assignments_dir,
        _skip_golden=True,
    )

    found = _pairs_from_assignments(assignments_dir)
    recall = _recall(found, gt)
    assert recall >= 0.9, (
        f"FS Phase-5 e2e (legacy, 1 partition) cluster recall {recall:.3f} < 0.9 "
        f"({len(found & gt)}/{len(gt)} GT pairs recovered)"
    )
