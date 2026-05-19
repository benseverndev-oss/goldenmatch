"""Distributed clustering via label propagation on Ray Datasets.

Phase 3 of the Splink-Spark parity roadmap. See
docs/superpowers/specs/2026-05-19-phase-3-distributed-clustering-design.md.

All ray imports are deferred to function bodies so module import succeeds
without the [ray] extra installed.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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


def build_clusters_distributed(
    pairs_ds: Dataset,
    all_ids: list[int],
    *,
    max_cluster_size: int = 100,
    weak_cluster_threshold: float = 0.3,
    convergence_max_iterations: int = 30,
) -> Dataset:
    """Distributed clustering. Returns a Ray Dataset of cluster assignments.

    Row shape: {member_id, cluster_id, cluster_size, oversized}.

    cluster_id is the label that label propagation converged to (the minimum
    member_id in the connected component). cluster_size is the count of
    members sharing that label.
    """
    import pyarrow as pa
    import ray

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
    """Driver-side scipy.csgraph fallback when label propagation fails."""
    import numpy as np
    import ray
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components

    pair_rows = pairs_ds.take_all()
    sorted_ids = sorted(all_ids)
    id_index = {i: idx for idx, i in enumerate(sorted_ids)}
    inv_id_index = {idx: i for i, idx in id_index.items()}

    row = [id_index[r["id_a"]] for r in pair_rows]
    col = [id_index[r["id_b"]] for r in pair_rows]
    data = [1] * len(pair_rows)
    n = len(id_index)
    graph = csr_matrix((data, (row, col)), shape=(n, n))
    n_components, labels = connected_components(graph, directed=False)

    sizes = np.bincount(labels)
    rows = []
    for idx, label in enumerate(labels):
        mid = inv_id_index[idx]
        size = int(sizes[label])
        rows.append({
            "member_id": mid,
            "cluster_id": int(label),
            "cluster_size": size,
            "oversized": size > max_cluster_size,
        })
    return ray.data.from_items(rows)


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
