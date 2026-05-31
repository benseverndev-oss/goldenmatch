"""Arrow-native roadmap Phase 5 (#627): identity per-partition resolver
building blocks.

This module provides the data-shape transformations that Phase 5's
``ray.map_batches`` wire-up needs at the boundary. Today it ships
``partition_cluster_frames`` (hash-by-cluster_id splitter) which
takes a ``ClusterFrames`` and emits N sub-frames each containing a
disjoint subset of cluster_ids. Used directly today for testing /
multi-worker simulation; consumed by the production
``ray.map_batches`` path once Phase 5's full wire-up lands.

Why per-partition: ``materialize_cluster_dict`` collects ~17M
cluster aggregates to ~3 GB driver-side at 25M scale (per
CLAUDE.md). Phase 5's binding lift is to make each Ray worker resolve
its OWN partition's identities against a pooled Postgres connection,
so the driver never holds the full cluster dict. The partitioner
gives each worker a deterministic, disjoint slice of cluster_ids.

Hash-by-cluster_id (not by member_id) so all members of one cluster
end up on the same partition -- the resolver's correctness depends
on seeing each cluster's full member set in one place.

Spec: docs/superpowers/specs/2026-05-31-arrow-native-roadmap.md
(gitignored).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from goldenmatch.core.cluster import ClusterFrames


def partition_cluster_frames(
    frames: ClusterFrames, num_partitions: int,
) -> list[ClusterFrames]:
    """Hash-partition a ``ClusterFrames`` into ``num_partitions`` disjoint
    slices by ``cluster_id``.

    Each output ``ClusterFrames`` carries a SUBSET of the input's
    cluster_ids; the union of subsets equals the original input (no
    cluster is dropped or duplicated). All members of one cluster
    land on the same partition -- the resolver needs the full member
    list per cluster to make correct identity decisions.

    Args:
        frames: Input ``ClusterFrames`` (Phase 2a shape).
        num_partitions: Number of disjoint slices to emit. Must be
            >= 1. Empty input yields ``num_partitions`` empty frames.

    Returns:
        List of ``ClusterFrames`` of length ``num_partitions``.
        Partition ``i`` contains ``cluster_id`` such that
        ``hash(cluster_id) % num_partitions == i``.

    Properties (locked by tests):
        - Disjoint: no cluster_id appears in more than one partition.
        - Complete: union of partition cluster_ids equals input.
        - Stable: same input produces same partition assignment (hash
          is deterministic; Polars' ``__hash__`` is based on bytewise
          contents on the i64 column).
    """
    import polars as _pl

    if num_partitions < 1:
        raise ValueError(
            f"num_partitions must be >= 1; got {num_partitions}"
        )

    empty_assignments_schema = {
        "cluster_id": _pl.Int64(),
        "member_id":  _pl.Int64(),
    }
    empty_metadata_schema = {
        "cluster_id":       _pl.Int64(),
        "size":             _pl.Int64(),
        "confidence":       _pl.Float64(),
        "quality":          _pl.Utf8(),
        "oversized":        _pl.Boolean(),
        "bottleneck_pair_a": _pl.Int64(),
        "bottleneck_pair_b": _pl.Int64(),
    }

    if frames.metadata.is_empty():
        return [
            ClusterFrames(
                assignments=_pl.DataFrame(schema=empty_assignments_schema),
                metadata=_pl.DataFrame(schema=empty_metadata_schema),
            )
            for _ in range(num_partitions)
        ]

    # Tag every cluster with its partition index via ``cluster_id %
    # num_partitions``. Polars supports modulo on integer columns
    # natively. For i64 cluster_ids, this gives a uniform partition
    # distribution as long as cluster_ids aren't pathologically
    # correlated with N (uuidv7 + offset minted ids satisfy this).
    metadata_tagged = frames.metadata.with_columns(
        (_pl.col("cluster_id") % num_partitions).alias("__partition__"),
    )

    # Build partition_id -> cluster_id table once, then join the
    # assignments frame against it so each (cluster, member) row
    # carries its partition. Doing this via join instead of recomputing
    # the hash keeps the partition assignment consistent if the modulo
    # logic ever moves (single source of truth: metadata_tagged).
    partition_index = metadata_tagged.select(["cluster_id", "__partition__"])
    assignments_tagged = frames.assignments.join(
        partition_index, on="cluster_id", how="inner",
    )

    out: list[ClusterFrames] = []
    for i in range(num_partitions):
        part_assignments = (
            assignments_tagged
            .filter(_pl.col("__partition__") == i)
            .drop("__partition__")
        )
        part_metadata = (
            metadata_tagged
            .filter(_pl.col("__partition__") == i)
            .drop("__partition__")
        )
        out.append(ClusterFrames(
            assignments=part_assignments,
            metadata=part_metadata,
        ))
    return out


def merge_partitioned_frames(parts: list[ClusterFrames]) -> ClusterFrames:
    """Inverse of ``partition_cluster_frames``: concatenate N partition
    frames back into a single ``ClusterFrames``.

    Used to verify the round-trip property (``merge(partition(x)) == x``)
    in tests and to consume per-worker results once the partitioned
    resolve_clusters path is wired through Ray.

    Args:
        parts: list of ``ClusterFrames`` to concatenate.

    Returns:
        A single ``ClusterFrames`` whose ``assignments`` and ``metadata``
        are the row-concatenation of each partition's frames.
    """
    import polars as _pl

    if not parts:
        # Caller should provide at least one partition; mirror the
        # ``partition_cluster_frames`` empty-input shape so the
        # round-trip is total.
        empty_assignments_schema = {
            "cluster_id": _pl.Int64(),
            "member_id":  _pl.Int64(),
        }
        empty_metadata_schema = {
            "cluster_id":       _pl.Int64(),
            "size":             _pl.Int64(),
            "confidence":       _pl.Float64(),
            "quality":          _pl.Utf8(),
            "oversized":        _pl.Boolean(),
            "bottleneck_pair_a": _pl.Int64(),
            "bottleneck_pair_b": _pl.Int64(),
        }
        return ClusterFrames(
            assignments=_pl.DataFrame(schema=empty_assignments_schema),
            metadata=_pl.DataFrame(schema=empty_metadata_schema),
        )
    assignments = _pl.concat([p.assignments for p in parts])
    metadata = _pl.concat([p.metadata for p in parts])
    return ClusterFrames(assignments=assignments, metadata=metadata)
