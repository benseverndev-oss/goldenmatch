"""Phase 5 building block: hash-by-cluster_id partitioning of
``ClusterFrames``.

GH issue #627 (Arrow-native roadmap Phase 5).

Tests the disjoint + complete + stable + cluster-cohesive properties
of ``partition_cluster_frames``. These are the contracts Phase 5's
``ray.map_batches`` will rely on when it shards cluster resolution
across workers.
"""
from __future__ import annotations

import polars as pl
from goldenmatch.core.cluster import ClusterFrames
from goldenmatch.distributed.identity_partition import (
    merge_partitioned_frames,
    partition_cluster_frames,
)


def _make_frames(n_clusters: int = 6) -> ClusterFrames:
    """Build n_clusters clusters with 2-3 members each."""
    cluster_ids = list(range(100, 100 + n_clusters))
    a_rows = []
    sizes = []
    for cid in cluster_ids:
        # cluster i has members [10*i, 10*i+1, 10*i+2] (3 members each)
        for m in range(10 * cid, 10 * cid + 3):
            a_rows.append((cid, m))
        sizes.append(3)
    assignments = pl.DataFrame({
        "cluster_id": pl.Series([r[0] for r in a_rows], dtype=pl.Int64),
        "member_id":  pl.Series([r[1] for r in a_rows], dtype=pl.Int64),
    })
    metadata = pl.DataFrame({
        "cluster_id": pl.Series(cluster_ids, dtype=pl.Int64),
        "size":       pl.Series(sizes, dtype=pl.Int64),
        "confidence": pl.Series([0.9] * n_clusters, dtype=pl.Float64),
        "quality":    pl.Series(["strong"] * n_clusters, dtype=pl.Utf8),
        "oversized":  pl.Series([False] * n_clusters, dtype=pl.Boolean),
        "bottleneck_pair_a": pl.Series([0] * n_clusters, dtype=pl.Int64),
        "bottleneck_pair_b": pl.Series([0] * n_clusters, dtype=pl.Int64),
    })
    return ClusterFrames(assignments=assignments, metadata=metadata)


class TestDisjoint:
    def test_no_cluster_id_in_two_partitions(self):
        frames = _make_frames(20)
        parts = partition_cluster_frames(frames, num_partitions=4)
        seen: set[int] = set()
        for p in parts:
            cids = set(p.metadata["cluster_id"].to_list())
            assert not (seen & cids), (
                "cluster_id appeared in multiple partitions; "
                f"overlap={seen & cids}"
            )
            seen.update(cids)


class TestComplete:
    def test_union_equals_input_cluster_ids(self):
        frames = _make_frames(20)
        parts = partition_cluster_frames(frames, num_partitions=4)
        union: set[int] = set()
        for p in parts:
            union.update(p.metadata["cluster_id"].to_list())
        original = set(frames.metadata["cluster_id"].to_list())
        assert union == original, (
            f"partitioner dropped clusters: missing={original - union}"
        )

    def test_union_equals_input_assignment_rows(self):
        frames = _make_frames(15)
        parts = partition_cluster_frames(frames, num_partitions=3)
        total_rows = sum(p.assignments.height for p in parts)
        assert total_rows == frames.assignments.height


class TestStable:
    def test_same_input_same_assignment(self):
        frames = _make_frames(10)
        parts_a = partition_cluster_frames(frames, num_partitions=3)
        parts_b = partition_cluster_frames(frames, num_partitions=3)
        for a, b in zip(parts_a, parts_b, strict=True):
            assert sorted(a.metadata["cluster_id"].to_list()) == \
                   sorted(b.metadata["cluster_id"].to_list())


class TestClusterCohesive:
    def test_all_members_of_one_cluster_in_same_partition(self):
        """The resolver depends on seeing every member of a cluster
        in one partition. Hash MUST be on cluster_id, not member_id."""
        frames = _make_frames(12)
        parts = partition_cluster_frames(frames, num_partitions=4)
        for p in parts:
            # Every member's cluster_id in this partition's assignments
            # MUST also appear in this partition's metadata.
            cid_assignments = set(p.assignments["cluster_id"].to_list())
            cid_metadata = set(p.metadata["cluster_id"].to_list())
            assert cid_assignments == cid_metadata, (
                f"cluster_id mismatch within partition: "
                f"assignments has {cid_assignments - cid_metadata} not in metadata, "
                f"metadata has {cid_metadata - cid_assignments} not in assignments"
            )


class TestEdgeCases:
    def test_empty_input(self):
        empty = ClusterFrames(
            assignments=pl.DataFrame({
                "cluster_id": pl.Series([], dtype=pl.Int64),
                "member_id":  pl.Series([], dtype=pl.Int64),
            }),
            metadata=pl.DataFrame({
                "cluster_id":       pl.Series([], dtype=pl.Int64),
                "size":             pl.Series([], dtype=pl.Int64),
                "confidence":       pl.Series([], dtype=pl.Float64),
                "quality":          pl.Series([], dtype=pl.Utf8),
                "oversized":        pl.Series([], dtype=pl.Boolean),
                "bottleneck_pair_a": pl.Series([], dtype=pl.Int64),
                "bottleneck_pair_b": pl.Series([], dtype=pl.Int64),
            }),
        )
        parts = partition_cluster_frames(empty, num_partitions=4)
        assert len(parts) == 4
        assert all(p.metadata.is_empty() for p in parts)
        assert all(p.assignments.is_empty() for p in parts)

    def test_num_partitions_one(self):
        """N=1 must return a single partition containing the full input."""
        frames = _make_frames(5)
        parts = partition_cluster_frames(frames, num_partitions=1)
        assert len(parts) == 1
        assert parts[0].metadata.height == frames.metadata.height
        assert parts[0].assignments.height == frames.assignments.height

    def test_invalid_num_partitions(self):
        frames = _make_frames(5)
        try:
            partition_cluster_frames(frames, num_partitions=0)
        except ValueError as e:
            assert "num_partitions" in str(e)
        else:
            raise AssertionError("expected ValueError for num_partitions=0")


class TestRoundTrip:
    def test_merge_of_partition_equals_input(self):
        """``merge_partitioned_frames(partition_cluster_frames(x, n)) == x``
        modulo row order."""
        frames = _make_frames(15)
        parts = partition_cluster_frames(frames, num_partitions=4)
        merged = merge_partitioned_frames(parts)
        # Compare as sorted sets of cluster_ids + row counts.
        assert sorted(merged.metadata["cluster_id"].to_list()) == \
               sorted(frames.metadata["cluster_id"].to_list())
        assert merged.assignments.height == frames.assignments.height
        assert merged.metadata.height == frames.metadata.height
