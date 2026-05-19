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
    import ray
    import pyarrow as pa

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
