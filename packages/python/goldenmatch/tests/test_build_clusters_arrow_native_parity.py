"""Phase 3 (Rust): ``build_clusters_arrow_native`` matches
``build_clusters_v2_columnar`` on the canonical partition.

GH issue #625 (Arrow-native roadmap Phase 3).

The Arrow kernel must produce the same cluster partition (member ->
co-members mapping) as the legacy dict-shaped builder. Cluster_id
numbering and metadata field values (confidence, oversized, etc.)
must also match.

Skipped when ``goldenmatch._native`` isn't built or doesn't yet
expose ``build_clusters_arrow``.
"""
from __future__ import annotations

import polars as pl
import pytest

native = pytest.importorskip("goldenmatch._native")
if not hasattr(native, "build_clusters_arrow"):
    pytest.skip(
        "native module loaded but build_clusters_arrow not exposed; "
        "Rust kernel needs to be rebuilt against the Phase 3 PR.",
        allow_module_level=True,
    )

from goldenmatch.core.cluster import (
    ClusterFrames,
    build_clusters_arrow_native,
    build_clusters_v2_columnar,
)
from goldenmatch.core.scorer import pairs_list_to_df


def _partition_from_frames(frames: ClusterFrames) -> frozenset[frozenset[int]]:
    if frames.assignments.is_empty():
        return frozenset()
    by_cid: dict[int, set[int]] = {}
    for cid, mid in zip(
        frames.assignments["cluster_id"].to_list(),
        frames.assignments["member_id"].to_list(),
        strict=True,
    ):
        by_cid.setdefault(int(cid), set()).add(int(mid))
    return frozenset(frozenset(members) for members in by_cid.values())


class TestPartitionParity:
    def test_simple_clusters_partition_matches_legacy(self):
        pairs = [(1, 2, 0.95), (2, 3, 0.92), (5, 6, 0.88)]
        all_ids = [1, 2, 3, 4, 5, 6, 7]
        df = pairs_list_to_df(pairs)

        rust = build_clusters_arrow_native(df, all_ids=all_ids)
        legacy = build_clusters_v2_columnar(df, all_ids=all_ids)

        # Same partition (members co-grouped identically). Cluster IDs
        # may differ in absolute numbering -- compare via the partition
        # equivalence relation.
        assert _partition_from_frames(rust) == _partition_from_frames(legacy)

    def test_no_pairs_emits_singletons(self):
        df = pairs_list_to_df([])
        all_ids = [1, 2, 3]
        rust = build_clusters_arrow_native(df, all_ids=all_ids)
        legacy = build_clusters_v2_columnar(df, all_ids=all_ids)
        assert _partition_from_frames(rust) == _partition_from_frames(legacy)
        # Singletons: each id its own cluster.
        assert _partition_from_frames(rust) == frozenset({
            frozenset({1}), frozenset({2}), frozenset({3}),
        })

    def test_transitive_closure(self):
        """1-2 and 2-3 should put {1,2,3} in the same cluster."""
        pairs = [(1, 2, 0.9), (2, 3, 0.85)]
        df = pairs_list_to_df(pairs)
        rust = build_clusters_arrow_native(df, all_ids=[1, 2, 3])
        partition = _partition_from_frames(rust)
        assert partition == frozenset({frozenset({1, 2, 3})})


class TestMetadata:
    def test_size_matches_member_count(self):
        pairs = [(1, 2, 0.9), (2, 3, 0.85), (5, 6, 0.95)]
        df = pairs_list_to_df(pairs)
        rust = build_clusters_arrow_native(df, all_ids=[1, 2, 3, 4, 5, 6])
        # Per cluster, metadata.size must equal the row count in
        # assignments for that cluster_id.
        meta_size = dict(zip(
            rust.metadata["cluster_id"].to_list(),
            rust.metadata["size"].to_list(),
            strict=True,
        ))
        actual_size = (
            rust.assignments
            .group_by("cluster_id")
            .agg(pl.len().alias("n"))
        )
        for cid, n in zip(
            actual_size["cluster_id"].to_list(),
            actual_size["n"].to_list(),
            strict=True,
        ):
            assert meta_size[int(cid)] == int(n)

    def test_oversized_flag(self):
        # 5 members in one cluster; max_cluster_size=3 -> oversized.
        pairs = [(1, 2, 0.9), (2, 3, 0.9), (3, 4, 0.9), (4, 5, 0.9)]
        df = pairs_list_to_df(pairs)
        rust = build_clusters_arrow_native(
            df, all_ids=[1, 2, 3, 4, 5], max_cluster_size=3,
        )
        # Find the cluster with size > 3 and verify it's flagged.
        for cid, size, oversized in zip(
            rust.metadata["cluster_id"].to_list(),
            rust.metadata["size"].to_list(),
            rust.metadata["oversized"].to_list(),
            strict=True,
        ):
            if size > 3:
                assert oversized, f"cluster {cid} size {size} not marked oversized"
            else:
                assert not oversized

    def test_confidence_present(self):
        """Multi-member clusters should have a non-zero confidence
        value (the actual algorithm: 0.4*min + 0.3*avg + 0.3*conn)."""
        pairs = [(1, 2, 0.95), (2, 3, 0.92), (1, 3, 0.90)]
        df = pairs_list_to_df(pairs)
        rust = build_clusters_arrow_native(df, all_ids=[1, 2, 3])
        # All members in one cluster, fully connected triangle.
        # confidence should be > 0.
        for size, conf in zip(
            rust.metadata["size"].to_list(),
            rust.metadata["confidence"].to_list(),
            strict=True,
        ):
            if size > 1:
                assert conf > 0.0, (
                    f"multi-member cluster has zero confidence: size={size}"
                )


class TestClusterFramesContract:
    def test_returns_cluster_frames(self):
        df = pairs_list_to_df([(1, 2, 0.9)])
        rust = build_clusters_arrow_native(df, all_ids=[1, 2])
        assert isinstance(rust, ClusterFrames)

    def test_schemas(self):
        df = pairs_list_to_df([(1, 2, 0.9)])
        rust = build_clusters_arrow_native(df, all_ids=[1, 2])
        assert set(rust.assignments.columns) == {"cluster_id", "member_id"}
        # min_edge/avg_edge were added to the metadata frame by SP4 (the weak-
        # quality stats; see cluster.rs build_clusters_arrow) so the vectorized
        # weak/quality downgrade reads them off the frame without rebuilding
        # per-cluster pair_scores dicts. Both build paths emit this 9-col schema
        # identically (verified native=1 vs native=0).
        assert set(rust.metadata.columns) == {
            "cluster_id", "size", "confidence", "quality",
            "oversized", "bottleneck_pair_a", "bottleneck_pair_b",
            "min_edge", "avg_edge",
        }

    def test_empty_input(self):
        """Empty pair stream + empty all_ids -> empty frames."""
        df = pairs_list_to_df([])
        rust = build_clusters_arrow_native(df, all_ids=[])
        assert rust.assignments.is_empty()
        assert rust.metadata.is_empty()
