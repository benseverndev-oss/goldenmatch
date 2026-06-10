"""Distributed clustering via label propagation on Ray Datasets.

Phase 3 of the Splink-Spark parity roadmap. See
docs/superpowers/specs/2026-05-19-phase-3-distributed-clustering-design.md.

All ray imports are deferred to function bodies so module import succeeds
without the [ray] extra installed.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ray.data import Dataset

logger = logging.getLogger(__name__)


class ConvergenceError(Exception):
    """Raised when label propagation fails to converge within the iteration cap."""
    pass


def pairs_list_to_dataset(
    pairs: list[tuple[int, int, float]],
) -> Dataset:
    """Convert in-memory scored pairs to a Ray Dataset.

    Each row: {"id_a": int, "id_b": int, "score": float}.
    """
    import ray

    if not ray.is_initialized():
        ray.init(ignore_reinit_error=True, log_to_driver=False)
    rows = [{"id_a": a, "id_b": b, "score": s} for a, b, s in pairs]
    return ray.data.from_items(rows)


def _propagate_one_step(pairs_ds: Dataset, labels_ds: Dataset) -> Dataset:
    """One label propagation step.

    Each pair (id_a, id_b) emits two proposals: (id_a, min(label_a, label_b))
    and (id_b, min(label_a, label_b)). Each id then takes the min of its
    current label and all proposals it received.
    """
    import pyarrow as pa
    import ray

    # Materialize labels to a small dict broadcast via object store.
    label_rows = labels_ds.take_all()
    label_map = {r["id"]: r["label"] for r in label_rows}
    label_map_ref = ray.put(label_map)

    def _emit_proposals(batch: pa.Table) -> pa.Table:
        lm = ray.get(label_map_ref)
        out = []
        for row in batch.to_pylist():
            a, b = row["id_a"], row["id_b"]
            la = lm.get(a, a)
            lb = lm.get(b, b)
            mn = min(la, lb)
            out.append({"id": a, "label": mn})
            out.append({"id": b, "label": mn})
        return pa.Table.from_pylist(out)

    proposals_ds = pairs_ds.map_batches(_emit_proposals, batch_format="pyarrow")
    self_labels = ray.data.from_items(label_rows)
    combined = proposals_ds.union(self_labels)

    new_labels = combined.groupby("id").min("label")

    # Normalize column name (Ray's groupby.min output column varies by version)
    def _rename(batch: pa.Table) -> pa.Table:
        cols = batch.column_names
        # Find the min(label) or label_min column and rename to "label"
        new_cols = []
        for c in cols:
            if c == "id":
                new_cols.append("id")
            else:
                new_cols.append("label")
        return batch.rename_columns(new_cols)

    return new_labels.map_batches(_rename, batch_format="pyarrow")


def label_propagation(
    pairs_ds: Dataset,
    all_ids: list[int],
    *,
    convergence_max_iterations: int = 30,
) -> tuple[Dataset, int]:
    """Run label propagation to fixed point.

    Returns (labels_dataset, iterations_taken). Raises ConvergenceError if
    not converged within convergence_max_iterations.
    """
    import ray

    if not ray.is_initialized():
        ray.init(ignore_reinit_error=True, log_to_driver=False)

    labels_ds = ray.data.from_items([{"id": i, "label": i} for i in all_ids])
    prev_map = {i: i for i in all_ids}

    for iteration in range(1, convergence_max_iterations + 1):
        labels_ds = _propagate_one_step(pairs_ds, labels_ds)
        new_rows = labels_ds.take_all()
        new_map = {r["id"]: r["label"] for r in new_rows}

        if new_map == prev_map:
            return labels_ds, iteration
        prev_map = new_map

    raise ConvergenceError(
        f"label propagation did not converge in {convergence_max_iterations} iterations"
    )


# Pair-count threshold above which we route to distributed label propagation.
# Below this, driver-side scipy.csgraph dominates (per run 26119800863:
# label-prop on 8.3M pairs ran > 14 min; scipy on the same shape would be
# seconds). Splink-Spark follows the same pattern: DuckDB backend below
# the scale where Spark is necessary; Spark above.
#
# 50M chosen as a conservative threshold:
#  - 50M pairs = ~1.2 GB driver memory for the (int64, int64, float64) triple
#  - scipy.csgraph on that scale: ~30-60s, manageable on 64 GB box
#  - Above 50M, driver materialization starts to compete with Ray's overhead
# Override via GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD env var (pairs).
_LABEL_PROP_PAIR_THRESHOLD = 50_000_000

# Mersenne prime for the randomized-contraction affine hash (#844). Vertex ids
# must be < _RC_PRIME; row ids at 346M are ~2**28, well under 2**31-1 ~= 2.1B.
# Keeps h(x)=(A*x+B) % p in i64 range: A,x,B < 2**31 => A*x < 2**62 < 2**63.
_RC_PRIME = 2**31 - 1


def _wcc_algorithm() -> str:
    """Read GOLDENMATCH_DISTRIBUTED_WCC env var (default 'two_phase').

    NOTE: the at-scale pipeline (``GOLDENMATCH_DISTRIBUTED_PIPELINE=2``) does NOT
    route through this function -- it uses ``local_cc_assignments`` directly,
    which has no driver collect and is what scales to 100M. This selector only
    governs ``build_clusters_distributed``'s own callers, where 'two_phase'
    stays the default (Sem Sinchenko recommendation; partition-sensitive but
    correct). 'pointer_jump' (``distributed_wcc``) is OPT-IN only -- its
    iterative ``Dataset.join`` loop can deadlock Ray's streaming executor at
    scale, and it's unnecessary because scoring is per-partition (no
    cross-partition components to merge). 'label_propagation' is also available.
    """
    import os
    return os.environ.get("GOLDENMATCH_DISTRIBUTED_WCC", "two_phase").lower()


def _label_prop_threshold() -> int:
    import os

    raw = os.environ.get("GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD")
    if raw is None:
        return _LABEL_PROP_PAIR_THRESHOLD
    try:
        return int(raw)
    except ValueError:
        return _LABEL_PROP_PAIR_THRESHOLD


def _derive_touched_ids(pairs_ds: Dataset) -> list[int]:
    """Distinct ids appearing in any pair (id_a or id_b), sorted.

    Driver-side collect of the distinct SET -- used ONLY on the scipy and
    label-propagation routes, which are gated below the 50M-pair threshold,
    so the set is bounded. The two_phase_wcc route never calls this (it takes
    all_ids=None and derives touched members distributively in Phase A).
    """
    seen: set[int] = set()
    for batch in pairs_ds.iter_batches(batch_format="pyarrow"):
        seen.update(batch.column("id_a").to_pylist())
        seen.update(batch.column("id_b").to_pylist())
    return sorted(seen)


def build_clusters_distributed(
    pairs_ds: Dataset,
    all_ids: list[int] | None = None,
    *,
    max_cluster_size: int = 100,
    weak_cluster_threshold: float = 0.3,
    convergence_max_iterations: int = 30,
    force_label_propagation: bool = False,
) -> Dataset:
    """Distributed clustering. Returns a Ray Dataset of cluster assignments.

    Row shape: {member_id, cluster_id, cluster_size, oversized}.

    Routing (Splink-Spark style):
      - Pair count below threshold (default 50M): driver-side scipy.csgraph.
        Faster than distributed label propagation until the pair list stops
        fitting in driver memory.
      - Pair count above threshold OR force_label_propagation=True:
        distributed label propagation on Ray Datasets. Falls back to scipy
        on non-convergence.

    Override threshold via env var GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD.

    ``all_ids`` is the full id universe (incl. isolated singletons). Pass
    ``None`` (default) for the golden scale path: only multi-member clusters
    are needed, so the two_phase route derives touched members distributively
    and never builds an O(N) driver id list. The scipy / label-propagation
    routes (small scale) derive the touched set on demand when all_ids is None.

    cluster_id is the minimum member_id in the connected component.
    cluster_size is the count of members sharing that label.
    """
    threshold = _label_prop_threshold()
    pair_count = pairs_ds.count()
    use_label_prop = force_label_propagation or pair_count >= threshold

    if not use_label_prop:
        logger.info(
            "build_clusters_distributed: %d pairs < %d threshold; "
            "routing to scipy.csgraph (driver-side, faster at this scale).",
            pair_count, threshold,
        )
        ids = all_ids if all_ids is not None else _derive_touched_ids(pairs_ds)
        return _annotate_cluster_sizes(
            _build_clusters_scipy_fallback(pairs_ds, ids, max_cluster_size),
            max_cluster_size,
            already_sized=True,
        )

    algorithm = _wcc_algorithm() if not force_label_propagation else "label_propagation"

    if algorithm == "label_propagation":
        logger.info(
            "build_clusters_distributed: %d pairs >= %d threshold; "
            "routing to distributed label propagation (env override or "
            "force_label_propagation=True).",
            pair_count, threshold,
        )
        ids = all_ids if all_ids is not None else _derive_touched_ids(pairs_ds)
        try:
            labels_ds, _iters = label_propagation(
                pairs_ds, ids,
                convergence_max_iterations=convergence_max_iterations,
            )
        except ConvergenceError as e:
            logger.warning(
                "label propagation did not converge; scipy.csgraph fallback on driver. %s",
                e,
            )
            return _annotate_cluster_sizes(
                _build_clusters_scipy_fallback(pairs_ds, ids, max_cluster_size),
                max_cluster_size,
                already_sized=True,
            )
    elif algorithm == "two_phase":
        logger.info(
            "build_clusters_distributed: %d pairs >= %d threshold; "
            "routing to two_phase_wcc (driver-side Phase A/B; wedges the head "
            "at 100M -- kept for comparison).",
            pair_count, threshold,
        )
        labels_ds = two_phase_wcc(pairs_ds, all_ids)
    else:  # pointer_jump (default): fully distributed, no driver collect.
        logger.info(
            "build_clusters_distributed: %d pairs >= %d threshold; "
            "routing to distributed_wcc (pointer-jumping, no driver collect).",
            pair_count, threshold,
        )
        labels_ds = distributed_wcc(pairs_ds)

    return _annotate_cluster_sizes(labels_ds, max_cluster_size)


def _annotate_cluster_sizes(
    labels_ds: Dataset,
    max_cluster_size: int,
    *,
    already_sized: bool = False,
) -> Dataset:
    """Attach {member_id, cluster_id, cluster_size, oversized} to a labels
    Dataset DISTRIBUTIVELY -- no driver take_all, no broadcast size_map.

    The prior implementation ran ``labels_ds.groupby("label").count()`` (a
    single-partition HashAggregate that hangs at O(N) distinct labels -- see
    CLAUDE.md run 26131602938) then ``take_all()``'d the per-label sizes into
    a driver dict and broadcast it. With a correct global id space the
    distinct-label count is ~n_records/cluster_size (tens of millions at
    100M), which is exactly where that aggregate wedges.

    Instead: hash-partition on ``label`` so EVERY row of a component lands in
    one partition, then count within the partition (== global cluster size).
    Same co-location trick build_golden_records_distributed uses. Bounded
    per-partition memory, no driver-side O(clusters) structure.

    ``already_sized=True`` short-circuits for the scipy fallback, which
    already emits {member_id, cluster_id, cluster_size, oversized}.
    """
    import os

    import polars as pl

    if already_sized:
        return labels_ds

    cpu = os.cpu_count() or 16
    num_partitions = min(256, max(4, cpu * 4))
    colocated = labels_ds.repartition(num_partitions, keys=["label"])

    def _emit(batch: Any) -> Any:  # pa.Table -> pa.Table
        df = pl.from_arrow(batch)
        assert isinstance(df, pl.DataFrame)
        if df.height == 0:
            # Preserve the output schema on empty partitions.
            empty = pl.DataFrame(
                schema={
                    "member_id": pl.Int64, "cluster_id": pl.Int64,
                    "cluster_size": pl.Int64, "oversized": pl.Boolean,
                },
            )
            return empty.to_arrow()
        # repartition(keys=["label"]) co-locates a whole component here, so
        # the within-partition per-label count is the GLOBAL cluster size.
        sized = df.with_columns(
            pl.len().over("label").alias("cluster_size"),
        )
        out = sized.select(
            pl.col("id").cast(pl.Int64).alias("member_id"),
            pl.col("label").cast(pl.Int64).alias("cluster_id"),
            pl.col("cluster_size").cast(pl.Int64),
            (pl.col("cluster_size") > max_cluster_size).alias("oversized"),
        )
        return out.to_arrow()

    return colocated.map_batches(_emit, batch_format="pyarrow")


def _build_clusters_scipy_fallback(
    pairs_ds: Dataset,
    all_ids: list[int],
    max_cluster_size: int,
) -> Dataset:
    """Driver-side scipy.csgraph fallback.

    Used for two paths in build_clusters_distributed:
      - default route below the 50M-pair threshold (Splink-DuckDB analog)
      - convergence-failure escape hatch when label propagation can't finish

    Vectorized end-to-end: pair rows -> Arrow columns -> numpy index lookup
    -> scipy connected_components -> Arrow output -> ray.data.from_arrow.
    Run 26122054424 had this at 67s on 8.3M pairs / 16.6M members; the
    naive Python-loop path drove that wall. Vectorized path targets <30s.
    """
    import numpy as np
    import polars as pl
    import pyarrow as pa
    import ray
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components

    # Pull pairs in pyarrow batches and concatenate (vectorized; the prior
    # take_all + per-row dict comprehension was the 67s bottleneck on
    # run 26122054424).
    pair_tables: list[pa.Table] = list(
        pairs_ds.iter_batches(batch_format="pyarrow")
    )
    if pair_tables:
        full = pa.concat_tables(pair_tables)
    else:
        full = pa.table({"id_a": [], "id_b": [], "score": []})
    id_a_arr = full.column("id_a").to_numpy(zero_copy_only=False)
    id_b_arr = full.column("id_b").to_numpy(zero_copy_only=False)

    # Build id -> dense index via numpy searchsorted (O(n_pairs log n_ids)
    # instead of dict[int, int].get per pair).
    sorted_ids = np.array(sorted(all_ids), dtype=np.int64)
    n = sorted_ids.shape[0]
    row_idx = np.searchsorted(sorted_ids, id_a_arr)
    col_idx = np.searchsorted(sorted_ids, id_b_arr)

    data = np.ones(row_idx.shape[0], dtype=np.int8)
    graph = csr_matrix((data, (row_idx, col_idx)), shape=(n, n))
    _n_components, labels = connected_components(graph, directed=False)

    # Compute per-cluster size via bincount; broadcast back to per-member.
    sizes_per_label = np.bincount(labels, minlength=int(labels.max()) + 1 if labels.size else 1)
    member_sizes = sizes_per_label[labels]
    oversized = member_sizes > max_cluster_size

    # Build output Arrow table column-at-a-time, then convert via from_arrow
    # (zero-copy into Ray Dataset; much cheaper than from_items on 16M dicts).
    out_table = pl.DataFrame(
        {
            "member_id": sorted_ids,
            "cluster_id": labels.astype(np.int64),
            "cluster_size": member_sizes.astype(np.int64),
            "oversized": oversized,
        }
    ).to_arrow()
    return ray.data.from_arrow(out_table)


def two_phase_wcc(
    pairs_ds: Dataset,
    all_ids: list[int] | None = None,
) -> Dataset:
    """Two-Phase Weakly Connected Components (Iverson et al, 2014).

    Phase A: per-partition local Union-Find (embarrassingly parallel).
    Phase B: cross-partition merge via super-graph UF on driver.

    Same output shape as label_propagation: a Ray Dataset of {id, label}
    rows where label is the min-id member of each connected component.

    Recommended by GraphFrames maintainer Sem Sinchenko for ER graphs
    because chains are label-prop's worst case but Phase B converges
    in O(1) iterations on chains.

    ``all_ids`` seeds ISOLATED nodes (ids that never appear in any pair) as
    their own singleton components. Pass ``None`` (the default) when the
    caller only needs MULTI-MEMBER clusters -- e.g. distributed golden, which
    drops singletons in the join anyway. Skipping it avoids materializing an
    O(N) Python id list on the driver (the whole point of the scale path):
    only pair-touched members flow through, derived distributively from
    Phase A. Passing a concrete list restores the full-universe behavior.
    """
    import polars as pl  # noqa: PLC0415
    import pyarrow as pa  # noqa: PLC0415
    import ray  # noqa: PLC0415

    if not ray.is_initialized():
        ray.init(ignore_reinit_error=True, log_to_driver=False)

    # Phase A: per-partition local CC. Stays a Ray Dataset all the way.
    local_ds = pairs_ds.map_batches(_phase_a_local_cc, batch_format="pyarrow")

    # Materialize Phase A output as a Polars frame on the driver. At 5M
    # members this is ~80 MiB; at 100M it's ~1.6 GiB, still fits a 64 GB
    # driver. The Ray Dataset to Polars conversion is one Arrow concat.
    phase_a_tables = list(local_ds.iter_batches(batch_format="pyarrow"))
    if phase_a_tables:
        local_components_pl = pl.from_arrow(pa.concat_tables(phase_a_tables))
    else:
        local_components_pl = pl.DataFrame(
            {"member_id": [], "local_root": []},
            schema={"member_id": pl.Int64, "local_root": pl.Int64},
        )

    # Seed isolated nodes (in all_ids but never touched by pairs). Skipped
    # entirely when all_ids is None: the golden scale path only needs
    # multi-member clusters, so enumerating the full id universe on the
    # driver (the O(N) materialization we're eliminating) is pure waste.
    if all_ids is not None:
        seen = set(local_components_pl["member_id"].to_list())
        isolated = [int(i) for i in all_ids if i not in seen]
        if isolated:
            seed_pl = pl.DataFrame(
                {"member_id": isolated, "local_root": isolated},
                schema={"member_id": pl.Int64, "local_root": pl.Int64},
            )
            local_components_pl = pl.concat([local_components_pl, seed_pl])

    # Phase B: returns a frame with {member_id, local_root, global_root}.
    components_pl = _phase_b_merge_boundaries(local_components_pl, pairs_ds)

    # Normalize label to min member_id per global root. Pure Polars groupby.
    labels_pl = (
        components_pl
        .group_by("global_root")
        .agg(pl.col("member_id").min().alias("label"))
    )
    final_pl = (
        components_pl
        .join(labels_pl, on="global_root", how="inner")
        .select(
            pl.col("member_id").cast(pl.Int64).alias("id"),
            pl.col("label").cast(pl.Int64),
        )
        # A member touched by pairs in >1 partition is emitted once per
        # partition by Phase A, so it appears multiple times in components_pl.
        # Phase B unions all of a member's local roots into one global_root, so
        # every duplicate carries the SAME label — dedup is safe and necessary:
        # without it, downstream groupby("label").count() inflates cluster_size
        # and materialize_cluster_dict duplicates members at partition
        # boundaries (a scale-only divergence from the in-memory components).
        .unique(subset=["id"], keep="first")
    )

    return ray.data.from_arrow(final_pl.to_arrow())


def _emit_boundary_pairs(batch: Any, member_to_root_ref: Any) -> Any:
    """Emit one row per boundary edge via vectorized Polars join.

    ``member_to_root_ref`` is one of:
      - ``pl.DataFrame`` with columns {member_id, local_root}: used by
        unit tests and by the production worker after deref.
      - ``dict[int, int]``: legacy unit-test shape; auto-converted to
        a Polars frame.
      - Ray ``ObjectRef`` to a Polars frame: production map_batches
        path; deref shares the Arrow buffer zero-copy from plasma.

    Polars frame at 5M entries is ~80 MiB resident; the equivalent
    Python dict is ~475 MiB and was the root cause of run 26166347530's
    SIGTERM (see docs/superpowers/specs/2026-05-20-two-phase-wcc-columnar-design.md).
    """
    import polars as pl  # noqa: PLC0415

    if isinstance(member_to_root_ref, pl.DataFrame):
        roots_pl = member_to_root_ref
    elif isinstance(member_to_root_ref, dict):
        roots_pl = pl.DataFrame({
            "member_id": list(member_to_root_ref.keys()),
            "local_root": list(member_to_root_ref.values()),
        })
    else:
        import ray  # noqa: PLC0415
        roots_pl = ray.get(member_to_root_ref)

    batch_pl = pl.from_arrow(batch).select(["id_a", "id_b"])

    joined = (
        batch_pl
        .join(
            roots_pl.rename({"member_id": "id_a", "local_root": "root_a"}),
            on="id_a", how="inner",
        )
        .join(
            roots_pl.rename({"member_id": "id_b", "local_root": "root_b"}),
            on="id_b", how="inner",
        )
        .filter(pl.col("root_a") != pl.col("root_b"))
        .select(["root_a", "root_b"])
    )

    return joined.to_arrow()


def _phase_b_merge_boundaries(
    local_components: Any,  # pl.DataFrame with columns {member_id, local_root}
    pairs_ds: Dataset,
) -> Any:  # pl.DataFrame with columns {member_id, local_root, global_root}
    """Phase B: reconcile local roots across partitions via super-graph UF.

    Returns a Polars frame with columns {member_id, local_root, global_root}.
    The downstream consumer in ``two_phase_wcc`` projects this to the final
    label per member.

    The driver-side UnionFind scales with n_distinct(local_roots), not with
    n_members. At 5M members + 16 partitions that's bounded by ~1.6M roots
    in the worst case, ~80 MB driver memory.

    ``ray.put(local_components)`` shares one Polars frame across all workers
    via the object store. The Arrow buffers behind the frame are mapped
    zero-copy from plasma into each worker, so per-worker resident cost is
    O(1) instead of the prior 475 MiB-per-worker Python dict rehydration.
    """
    import polars as pl  # noqa: PLC0415
    import ray  # noqa: PLC0415

    from goldenmatch.core.cluster import UnionFind

    local_components_pl: pl.DataFrame = local_components

    # ray.put once: shares one copy of local_components across all workers
    # via the object store. Polars frame at 5M entries is ~80 MiB vs a Python
    # dict's 475 MiB; Arrow buffers map zero-copy from plasma into workers.
    member_to_root_ref = ray.put(local_components_pl)

    boundary_tables = list(
        pairs_ds.map_batches(
            _emit_boundary_pairs,
            fn_kwargs={"member_to_root_ref": member_to_root_ref},
            batch_format="pyarrow",
        ).iter_batches(batch_format="pyarrow")
    )

    uf = UnionFind()
    # Seed UF with every distinct local_root so isolated components keep
    # their roots even when no boundary edge touches them.
    distinct_roots = local_components_pl["local_root"].unique().to_list()
    for root in distinct_roots:
        uf.add(int(root))

    for table in boundary_tables:
        for row in table.to_pylist():  # type: ignore[attr-defined]
            uf.add(int(row["root_a"]))
            uf.add(int(row["root_b"]))
            uf.union(int(row["root_a"]), int(row["root_b"]))

    # Build remap table once on driver (Polars frame, columnar).
    remap_pl = pl.DataFrame({
        "local_root": [int(r) for r in distinct_roots],
        "global_root": [int(uf.find(int(r))) for r in distinct_roots],
    })

    return (
        local_components_pl
        .join(remap_pl, on="local_root", how="left")
        .with_columns(
            global_root=pl.coalesce("global_root", "local_root"),
        )
    )


def _phase_a_local_cc(batch: Any) -> Any:  # batch: pa.Table -> pa.Table
    """Phase A of Two-Phase WCC: local Union-Find on this partition's pairs.

    Emits one (member_id, local_root) row per member touched by the
    partition's pairs. The local_root is meaningful only within the
    partition; Phase B reconciles roots across partitions.
    """
    import pyarrow as pa

    from goldenmatch.core.cluster import UnionFind

    uf = UnionFind()
    rows_in = batch.to_pylist()
    if not rows_in:
        return pa.Table.from_pylist([])

    for row in rows_in:
        uf.add(row["id_a"])
        uf.add(row["id_b"])
        uf.union(row["id_a"], row["id_b"])

    out = [{"member_id": m, "local_root": uf.find(m)} for m in uf.nodes()]
    return pa.Table.from_pylist(out)


def local_cc_assignments(
    pairs_ds: Dataset,
    *,
    max_cluster_size: int = 100,
) -> Dataset:
    """Connected components via per-partition local Union-Find -- a SINGLE
    embarrassingly-parallel ``map_batches``, no joins, no iteration, no driver
    collect. Returns ``{member_id, cluster_id, cluster_size, oversized}``.

    Correctness rests on one structural fact: distributed scoring is strictly
    per-partition (``score_blocks_distributed`` map_batches), so BOTH endpoints
    of every emitted pair were co-scored in the same partition. Connected
    components therefore never span partitions -- each component's edges are
    co-located in one block -- so a local Union-Find per block yields the global
    components directly. (A cluster split across an INPUT-partition boundary is
    scored as two components with no connecting edge; that boundary under-merge
    is the pipeline's pre-existing accepted coarseness, identical to what the
    per-partition scorer already produces.)

    ``cluster_id`` is the MIN member id of the component. Member ids are GLOBAL
    (the input carries ``__row_id__``), and components are disjoint, so the
    per-component min is globally unique -- no cross-partition relabelling
    needed. ``cluster_size`` is the component's member count (global, since the
    component is wholly within one block).

    MUST be fed the RAW scoring output (``score_blocks_distributed``), NOT the
    id_a-hash-shuffled ``dedup_pairs_distributed`` output -- that reshuffle
    splits a component's edges across blocks and would fragment it. Duplicate
    pairs are harmless (Union-Find union is idempotent), so dedup is unnecessary
    for clustering. ``batch_size=None`` keeps each block whole so a component is
    never split across sub-batches.

    This is the scale replacement for the distributed-WCC machinery
    (``two_phase_wcc``/``distributed_wcc``): two_phase collected Phase A members
    and Phase B boundary pairs to the DRIVER (wedged the head at 100M);
    distributed_wcc's iterative ``Dataset.join`` loop DEADLOCKED Ray's streaming
    executor (backpressure on ResourceBudget). Both are overkill -- there are no
    cross-partition components to merge.
    """
    from collections import defaultdict

    import polars as pl
    import pyarrow as pa

    from goldenmatch.core.cluster import UnionFind

    _SCHEMA = {
        "member_id": pl.Int64, "cluster_id": pl.Int64,
        "cluster_size": pl.Int64, "oversized": pl.Boolean,
    }

    def _cc(batch: Any) -> Any:  # pa.Table(pairs) -> pa.Table(assignments)
        df = pl.from_arrow(batch)
        if df.height == 0:
            return pl.DataFrame(schema=_SCHEMA).to_arrow()
        uf = UnionFind()
        a_list = df["id_a"].to_list()
        b_list = df["id_b"].to_list()
        for x, y in zip(a_list, b_list, strict=True):
            uf.add(x)
            uf.add(y)
            uf.union(x, y)
        comp: dict[int, list[int]] = defaultdict(list)
        for m in uf.nodes():
            comp[uf.find(m)].append(m)
        mids: list[int] = []
        cids: list[int] = []
        sizes: list[int] = []
        over: list[bool] = []
        for members in comp.values():
            label = min(members)
            size = len(members)
            is_over = size > max_cluster_size
            for m in members:
                mids.append(m)
                cids.append(label)
                sizes.append(size)
                over.append(is_over)
        return pa.table({
            "member_id": pa.array(mids, pa.int64()),
            "cluster_id": pa.array(cids, pa.int64()),
            "cluster_size": pa.array(sizes, pa.int64()),
            "oversized": pa.array(over, pa.bool_()),
        })

    return pairs_ds.map_batches(_cc, batch_format="pyarrow", batch_size=None)


def _wcc_groupby_min_label(ds: Dataset, num_partitions: int) -> Dataset:
    """Per-id min of ``label`` over a ``{id, label}`` Dataset, DISTRIBUTED.

    ``repartition(keys=['id'])`` co-locates every row sharing an id in one
    partition, so the per-partition Polars ``group_by('id').min()`` is the
    GLOBAL min -- the same co-location trick that dodges Ray's single-partition
    ``groupby().min()`` HashAggregate hang.
    """
    import polars as pl

    def _gmin(batch: Any) -> Any:
        df = pl.from_arrow(batch)
        if df.height == 0:
            return pl.DataFrame(
                schema={"id": pl.Int64, "label": pl.Int64},
            ).to_arrow()
        out = df.group_by("id").agg(pl.col("label").min())
        return out.select(
            pl.col("id").cast(pl.Int64), pl.col("label").cast(pl.Int64),
        ).to_arrow()

    return ds.repartition(num_partitions, keys=["id"]).map_batches(
        _gmin, batch_format="pyarrow",
    )


def distributed_wcc(pairs_ds: Dataset, *, max_iterations: int = 60) -> Dataset:
    """Fully-distributed Weakly Connected Components: min-label propagation with
    pointer-jumping (Shiloach-Vishkin shortcutting). Returns a Ray Dataset
    ``{id, label}`` where ``label`` is the MIN member id of each component --
    the same output contract as ``two_phase_wcc`` / ``label_propagation``.

    NO driver-side materialization. Every step is a Ray Data join or hash-
    shuffle groupby; convergence is a DISTRIBUTED change-count (one integer per
    round), not a ``take_all``. This is the scale replacement for
    ``two_phase_wcc``, whose Phase A collected all touched members to the driver
    (``list(local_ds.iter_batches())``) and whose Phase B collected ALL boundary
    pairs to the driver and ran the super-graph union-find there -- the exact
    driver-side collect that wedged the head at 100M (proven on a real GCP run:
    distributed scoring/dedup ran clean for 6.6 min, then the head wedged inside
    ``_emit_boundary_pairs``/Phase B while workers stayed healthy).

    Each round:
      (a) propagate: every edge sends ``label[src]`` to ``dst``; each vertex
          takes the min of its own label and all proposals (distributed groupby).
      (b) shortcut: ``label[v] := label[label[v]]`` (a self-join on the labels
          table) -- collapses pointer chains in O(log n) rounds. ER graphs are
          chain-heavy, the worst case for plain label propagation, which is why
          shortcutting (not bare propagation) is load-bearing here.
    Labels only ever decrease (min), so the iteration is monotone and the
    fixpoint -- ``label[v] == min(neighbours) == label[label[v]]`` for every v --
    is exactly the component min.
    """
    import polars as pl
    import ray

    if not ray.is_initialized():
        ray.init(ignore_reinit_error=True, log_to_driver=False)

    npart = min(256, max(4, (__import__("os").cpu_count() or 16) * 4))

    def _mk_edges(batch: Any) -> Any:
        df = pl.from_arrow(batch)
        if df.height == 0:
            return pl.DataFrame(
                schema={"src": pl.Int64, "dst": pl.Int64},
            ).to_arrow()
        fwd = df.select(
            pl.col("id_a").cast(pl.Int64).alias("src"),
            pl.col("id_b").cast(pl.Int64).alias("dst"),
        )
        bwd = df.select(
            pl.col("id_b").cast(pl.Int64).alias("src"),
            pl.col("id_a").cast(pl.Int64).alias("dst"),
        )
        return (
            pl.concat([fwd, bwd]).filter(pl.col("src") != pl.col("dst")).to_arrow()
        )

    edges = pairs_ds.map_batches(_mk_edges, batch_format="pyarrow").materialize()

    def _verts(batch: Any) -> Any:
        df = pl.from_arrow(batch)
        if df.height == 0:
            return pl.DataFrame(
                schema={"id": pl.Int64, "label": pl.Int64},
            ).to_arrow()
        ids = df.select(pl.col("src").alias("id")).unique()
        return ids.with_columns(
            pl.col("id").cast(pl.Int64).alias("label"),
        ).to_arrow()

    labels = _wcc_groupby_min_label(
        edges.map_batches(_verts, batch_format="pyarrow"), npart,
    ).materialize()

    def _proposal(batch: Any) -> Any:  # {src,dst,label} -> {id=dst, label}
        df = pl.from_arrow(batch)
        if df.height == 0:
            return pl.DataFrame(
                schema={"id": pl.Int64, "label": pl.Int64},
            ).to_arrow()
        return df.select(
            pl.col("dst").cast(pl.Int64).alias("id"),
            pl.col("label").cast(pl.Int64),
        ).to_arrow()

    def _shortcut_pick(batch: Any) -> Any:  # {id,label,label_p} -> {id, label}
        df = pl.from_arrow(batch)
        if df.height == 0:
            return pl.DataFrame(
                schema={"id": pl.Int64, "label": pl.Int64},
            ).to_arrow()
        return df.select(
            pl.col("id").cast(pl.Int64),
            pl.coalesce(["label_p", "label"]).cast(pl.Int64).alias("label"),
        ).to_arrow()

    def _changed(batch: Any) -> Any:  # {id,label,label_new} -> changed rows
        df = pl.from_arrow(batch)
        if df.height == 0:
            return df.to_arrow()
        return df.filter(pl.col("label") != pl.col("label_new")).to_arrow()

    for _it in range(max_iterations):
        # (a) propagate src->dst then min with current labels.
        j = edges.join(
            labels, join_type="inner", num_partitions=npart,
            on=("src",), right_on=("id",),
        )
        prop = j.map_batches(_proposal, batch_format="pyarrow")
        combined = prop.union(labels)
        newl = _wcc_groupby_min_label(combined, npart)
        # (b) pointer-jump: label[v] := label[label[v]].
        sc = newl.join(
            newl, join_type="left_outer", num_partitions=npart,
            on=("label",), right_on=("id",), right_suffix="_p",
        )
        newl2 = sc.map_batches(_shortcut_pick, batch_format="pyarrow").materialize()
        # convergence: distributed count of vertices whose label changed.
        cmp = labels.join(
            newl2, join_type="inner", num_partitions=npart,
            on=("id",), right_on=("id",), right_suffix="_new",
        )
        changed = cmp.map_batches(_changed, batch_format="pyarrow").count()
        labels = newl2
        logger.info("distributed_wcc round %d: changed=%d", _it + 1, changed)
        if changed == 0:
            break
    else:
        logger.warning(
            "distributed_wcc: hit max_iterations=%d without convergence; "
            "returning best-effort labels.", max_iterations,
        )

    return labels


# ---------------------------------------------------------------------------
# Randomized-contraction WCC (#844) — pure-Polars reference implementation
# ---------------------------------------------------------------------------

def _rc_symmetrize(pairs_pl: Any) -> Any:  # pl.DataFrame{id_a,id_b,...} -> pl.DataFrame{v,w}
    """Both-directions edge table, self-loops dropped, deduped."""
    import polars as pl
    fwd = pairs_pl.select(v=pl.col("id_a").cast(pl.Int64), w=pl.col("id_b").cast(pl.Int64))
    bwd = pairs_pl.select(v=pl.col("id_b").cast(pl.Int64), w=pl.col("id_a").cast(pl.Int64))
    return pl.concat([fwd, bwd]).filter(pl.col("v") != pl.col("w")).unique()


def _rc_contract_round(edges_pl: Any, A: int, B: int, p: int = _RC_PRIME):
    """One randomized-contraction round on a symmetrized edge table.

    Returns (contracted_edges_pl{v,w}, rep_pl{v,rep}). ``rep(v)`` is the vertex
    in v's CLOSED neighbourhood with the minimum affine hash h(u)=(A*u+B) % p.
    Contracted edges map both endpoints to their rep and drop self-loops.
    """
    import polars as pl
    nbr = edges_pl.select("v", u=pl.col("w"))
    selfv = edges_pl.select("v").unique().with_columns(u=pl.col("v"))
    cand = pl.concat([nbr, selfv]).with_columns(
        hu=((A * pl.col("u") + B) % p),
    )
    rep = (
        cand.sort("hu")
        .group_by("v", maintain_order=False)
        .agg(pl.col("u").first().alias("rep"))
    )
    rep_v = rep.select(v="v", rv="rep")
    rep_w = rep.select(w="v", rw="rep")
    contracted = (
        edges_pl
        .join(rep_v, on="v", how="inner")
        .join(rep_w, on="w", how="inner")
        .filter(pl.col("rv") != pl.col("rw"))
        .select(v=pl.col("rv"), w=pl.col("rw"))
        .unique()
    )
    return contracted, rep


def _rc_compose_labels(label_pl: Any, rep_pl: Any) -> Any:
    """Fold one round's rep map into the running orig_id -> current-rep map."""
    import polars as pl
    return (
        label_pl
        .join(rep_pl, left_on="cur", right_on="v", how="left")
        .with_columns(cur=pl.coalesce(["rep", "cur"]))
        .select("orig_id", "cur")
    )


def _rc_normalize_to_min_member(label_pl: Any) -> Any:  # -> pl.DataFrame{id,label}
    """Relabel each component by its MIN original member id (the cluster_id contract)."""
    import polars as pl
    mins = label_pl.group_by("cur").agg(pl.col("orig_id").min().alias("label"))
    return (
        label_pl.join(mins, on="cur", how="inner")
        .select(id=pl.col("orig_id").cast(pl.Int64), label=pl.col("label").cast(pl.Int64))
    )


def _rc_wcc_polars(pairs_pl: Any, *, seed: int | None = None, max_rounds: int = 80,
                   p: int = _RC_PRIME) -> Any:  # -> pl.DataFrame{id,label}
    """Pure-Polars randomized-contraction WCC. The reference implementation and
    the correctness gate; the Ray path mirrors this distributively.

    Raises ValueError if any vertex id is >= p (the affine hash needs ids < p).
    """
    import random

    import polars as pl

    E = _rc_symmetrize(pairs_pl)
    if E.height == 0:
        return pl.DataFrame(schema={"id": pl.Int64, "label": pl.Int64})
    max_id = max(E["v"].max(), E["w"].max())
    if max_id >= p:
        raise ValueError(
            f"randomized_contraction_wcc: vertex id {max_id} >= prime {p}; "
            "ids must be < 2**31-1 (future: 64-bit field)."
        )
    rng = random.Random(seed)
    label = E.select(orig_id="v").unique().with_columns(cur=pl.col("orig_id"))
    for _ in range(max_rounds):
        if E.height == 0:
            break
        A = rng.randrange(1, p)
        B = rng.randrange(0, p)
        E, rep = _rc_contract_round(E, A, B, p)
        label = _rc_compose_labels(label, rep)
    else:
        raise RuntimeError(
            f"randomized_contraction_wcc did not converge in {max_rounds} rounds "
            f"({E.height} edges remain) — investigate, do not silently truncate."
        )
    return _rc_normalize_to_min_member(label)


def materialize_cluster_dict(
    clusters_ds: Dataset,
    pairs_ds: Dataset,
) -> dict[int, dict]:
    """Convert distributed cluster output back to dict[int, dict] for
    back-compat with golden, identity, output.

    Phase 4 removes this adapter.
    """
    cluster_rows = clusters_ds.take_all()
    pair_rows = pairs_ds.take_all()

    raw_ids_sorted = sorted({r["cluster_id"] for r in cluster_rows})
    id_remap = {raw: new for new, raw in enumerate(raw_ids_sorted, start=1)}

    members_by_cluster: dict[int, list[int]] = {}
    size_by_cluster: dict[int, int] = {}
    oversized_by_cluster: dict[int, bool] = {}
    for r in cluster_rows:
        cid = id_remap[r["cluster_id"]]
        members_by_cluster.setdefault(cid, []).append(r["member_id"])
        size_by_cluster[cid] = r["cluster_size"]
        oversized_by_cluster[cid] = r["oversized"]

    member_to_cid: dict[int, int] = {}
    for cid, members in members_by_cluster.items():
        for m in members:
            member_to_cid[m] = cid

    result: dict[int, dict] = {}
    for cid, members in members_by_cluster.items():
        result[cid] = {
            "members": sorted(members),
            "size": size_by_cluster[cid],
            "oversized": oversized_by_cluster[cid],
            "pair_scores": {},
        }

    for r in pair_rows:
        a, b, s = r["id_a"], r["id_b"], r["score"]
        cid = member_to_cid.get(a)
        if cid is not None:
            result[cid]["pair_scores"][(a, b)] = s

    _attach_quality_metadata(result)
    return result


def _attach_quality_metadata(clusters: dict[int, dict]) -> None:
    """Populate confidence, bottleneck_pair, cluster_quality on each cluster.

    Mirrors the in-memory build_clusters semantics. Mutates in place.
    """
    for cinfo in clusters.values():
        scores = list(cinfo["pair_scores"].values())
        if not scores:
            cinfo["confidence"] = 1.0
            cinfo["bottleneck_pair"] = None
            cinfo["cluster_quality"] = "strong"
            continue
        min_edge = min(scores)
        avg_edge = sum(scores) / len(scores)
        connectivity = avg_edge
        cinfo["confidence"] = 0.4 * min_edge + 0.3 * avg_edge + 0.3 * connectivity
        weakest = min(cinfo["pair_scores"].items(), key=lambda kv: kv[1])
        cinfo["bottleneck_pair"] = weakest[0]
        cinfo["cluster_quality"] = "weak" if cinfo["confidence"] < 0.3 else "strong"
