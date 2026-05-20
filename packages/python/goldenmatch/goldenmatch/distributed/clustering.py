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


def _wcc_algorithm() -> str:
    """Read GOLDENMATCH_DISTRIBUTED_WCC env var (default 'two_phase')."""
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


def build_clusters_distributed(
    pairs_ds: Dataset,
    all_ids: list[int],
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

    cluster_id is the minimum member_id in the connected component.
    cluster_size is the count of members sharing that label.
    """
    import pyarrow as pa
    import ray

    threshold = _label_prop_threshold()
    pair_count = pairs_ds.count()
    use_label_prop = force_label_propagation or pair_count >= threshold

    if not use_label_prop:
        logger.info(
            "build_clusters_distributed: %d pairs < %d threshold; "
            "routing to scipy.csgraph (driver-side, faster at this scale).",
            pair_count, threshold,
        )
        return _build_clusters_scipy_fallback(pairs_ds, all_ids, max_cluster_size)

    algorithm = _wcc_algorithm() if not force_label_propagation else "label_propagation"

    if algorithm == "label_propagation":
        logger.info(
            "build_clusters_distributed: %d pairs >= %d threshold; "
            "routing to distributed label propagation (env override or "
            "force_label_propagation=True).",
            pair_count, threshold,
        )
        try:
            labels_ds, _iters = label_propagation(
                pairs_ds, all_ids,
                convergence_max_iterations=convergence_max_iterations,
            )
        except ConvergenceError as e:
            logger.warning(
                "label propagation did not converge; scipy.csgraph fallback on driver. %s",
                e,
            )
            return _build_clusters_scipy_fallback(pairs_ds, all_ids, max_cluster_size)
    else:
        logger.info(
            "build_clusters_distributed: %d pairs >= %d threshold; "
            "routing to two_phase_wcc (default, Sem Sinchenko recommendation).",
            pair_count, threshold,
        )
        labels_ds = two_phase_wcc(pairs_ds, all_ids)

    sizes_ds = labels_ds.groupby("label").count()
    size_rows = sizes_ds.take_all()
    size_map: dict[int, int] = {}
    for r in size_rows:
        for k, v in r.items():
            if k != "label" and "count" in k.lower():
                size_map[r["label"]] = v
                break

    size_map_ref = ray.put(size_map)

    def _emit_cluster_rows(batch: pa.Table) -> pa.Table:
        sm = ray.get(size_map_ref)
        out = []
        for row in batch.to_pylist():
            mid = row["id"]
            label = row["label"]
            size = sm.get(label, 1)
            out.append({
                "member_id": mid,
                "cluster_id": label,
                "cluster_size": size,
                "oversized": size > max_cluster_size,
            })
        return pa.Table.from_pylist(out)

    return labels_ds.map_batches(_emit_cluster_rows, batch_format="pyarrow")


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
    all_ids: list[int],
) -> Dataset:
    """Two-Phase Weakly Connected Components (Iverson et al, 2014).

    Phase A: per-partition local Union-Find (embarrassingly parallel).
    Phase B: cross-partition merge via super-graph UF on driver.

    Same output shape as label_propagation: a Ray Dataset of {id, label}
    rows where label is the min-id member of each connected component.

    Recommended by GraphFrames maintainer Sem Sinchenko for ER graphs
    because chains are label-prop's worst case but Phase B converges
    in O(1) iterations on chains.
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

    # Seed isolated nodes (in all_ids but never touched by pairs).
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
