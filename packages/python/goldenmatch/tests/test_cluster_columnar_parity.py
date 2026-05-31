"""Phase 2a parity tests for the two-frame cluster representation.

GH issue #624 (Arrow-native roadmap Phase 2).

Asserts ``cluster_dict_to_frames`` and ``cluster_frames_to_dict`` are
lossless round-trips, and that ``build_clusters_v2_columnar`` produces
the same partition (member-to-cluster mapping) as
``build_clusters_columnar`` (and therefore as ``build_clusters``,
transitively verified in test_pair_stream_columnar_parity.py).

Note: ``pair_scores`` is intentionally omitted from the round-trip —
it doesn't live on the cluster frame in Phase 2 (consumers compute it
via a lazy view against the Phase-1 pair stream, Phase 2b deliverable).
Tests assert this is the only diff between input and round-tripped
dict.
"""
from __future__ import annotations

import polars as pl
from goldenmatch.core.cluster import (
    ClusterFrames,
    build_clusters,
    build_clusters_v2_columnar,
    cluster_dict_to_frames,
    cluster_frames_to_dict,
)
from goldenmatch.core.scorer import PAIR_STREAM_SCHEMA, pairs_list_to_df

# ── Adapter round-trip ──────────────────────────────────────────────


class TestAdapterRoundtrip:
    def test_empty_clusters_to_frames(self):
        frames = cluster_dict_to_frames({})
        assert isinstance(frames, ClusterFrames)
        assert frames.assignments.is_empty()
        assert frames.metadata.is_empty()
        assert frames.assignments.schema == {
            "cluster_id": pl.Int64(), "member_id": pl.Int64(),
        }

    def test_empty_frames_to_clusters(self):
        frames = cluster_dict_to_frames({})
        assert cluster_frames_to_dict(frames) == {}

    def test_roundtrip_simple(self):
        clusters = {
            0: {
                "members": [1, 2, 3],
                "size": 3,
                "confidence": 0.95,
                "cluster_quality": "strong",
                "oversized": False,
                "bottleneck_pair": (1, 2),
                "pair_scores": {(1, 2): 0.95, (2, 3): 0.93, (1, 3): 0.90},
            },
            1: {
                "members": [5, 6],
                "size": 2,
                "confidence": 0.88,
                "cluster_quality": "weak",
                "oversized": False,
                "bottleneck_pair": (5, 6),
                "pair_scores": {(5, 6): 0.88},
            },
        }
        frames = cluster_dict_to_frames(clusters)
        back = cluster_frames_to_dict(frames)

        # Members lists match (modulo order — assert as sets).
        for cid in clusters:
            assert set(back[cid]["members"]) == set(clusters[cid]["members"])
            assert back[cid]["size"] == clusters[cid]["size"]
            assert back[cid]["confidence"] == clusters[cid]["confidence"]
            assert back[cid]["cluster_quality"] == clusters[cid]["cluster_quality"]
            assert back[cid]["oversized"] == clusters[cid]["oversized"]
            assert back[cid]["bottleneck_pair"] == clusters[cid]["bottleneck_pair"]
            # pair_scores intentionally NOT round-tripped (Phase 2b deliverable)
            assert back[cid]["pair_scores"] == {}

    def test_roundtrip_oversized_cluster(self):
        clusters = {
            42: {
                "members": list(range(101)),
                "size": 101,
                "confidence": 0.5,
                "cluster_quality": "split",
                "oversized": True,
                "bottleneck_pair": (10, 50),
                "pair_scores": {},
            },
        }
        frames = cluster_dict_to_frames(clusters)
        back = cluster_frames_to_dict(frames)
        assert back[42]["oversized"] is True
        assert back[42]["cluster_quality"] == "split"
        assert len(back[42]["members"]) == 101


# ── End-to-end: build_clusters_v2_columnar partition parity ────────


class TestBuildClustersV2Parity:
    def test_simple_clusters_partition_matches_legacy(self):
        pairs = [(1, 2, 0.95), (2, 3, 0.92), (5, 6, 0.88)]
        all_ids = [1, 2, 3, 4, 5, 6, 7]

        legacy = build_clusters(pairs, all_ids=all_ids)
        v2 = build_clusters_v2_columnar(
            pairs_list_to_df(pairs),
            all_ids=all_ids,
        )

        # Compare partitions (member -> set of co-members) — invariant
        # under cluster_id relabeling.
        legacy_partition = _partition_from_dict(legacy)
        v2_partition = _partition_from_frames(v2)
        assert legacy_partition == v2_partition

    def test_metadata_size_matches_member_count(self):
        pairs = [(1, 2, 0.9), (3, 4, 0.85), (5, 6, 0.9), (5, 7, 0.85)]
        v2 = build_clusters_v2_columnar(pairs_list_to_df(pairs))

        # Each metadata row's size must equal the assignments row count
        # for the same cluster_id. Phase 2 invariant.
        meta_size = dict(zip(
            v2.metadata["cluster_id"].to_list(),
            v2.metadata["size"].to_list(),
            strict=True,
        ))
        actual_size = (
            v2.assignments.group_by("cluster_id").agg(pl.len().alias("n"))
        )
        for cid, n in zip(
            actual_size["cluster_id"].to_list(),
            actual_size["n"].to_list(),
            strict=True,
        ):
            assert meta_size[int(cid)] == int(n), (
                f"metadata.size mismatch for cluster {cid}: "
                f"metadata says {meta_size[int(cid)]}, "
                f"assignments has {n} members"
            )

    def test_empty_pair_stream_v2(self):
        v2 = build_clusters_v2_columnar(
            pl.DataFrame(schema=PAIR_STREAM_SCHEMA),
            all_ids=[1, 2, 3],
        )
        # 3 singleton clusters expected (one per all_ids entry).
        n_clusters = v2.metadata.height
        assert n_clusters == 3
        # Each cluster has exactly one member.
        assert v2.assignments.height == 3


# ── Helpers ──────────────────────────────────────────────────────────


def _partition_from_dict(clusters: dict[int, dict]) -> frozenset[frozenset[int]]:
    return frozenset(
        frozenset(c.get("members", []))
        for c in clusters.values()
    )


def _partition_from_frames(frames: ClusterFrames) -> frozenset[frozenset[int]]:
    if frames.assignments.is_empty():
        return frozenset()
    by_cid: dict[int, list[int]] = {}
    for cid, mid in zip(
        frames.assignments["cluster_id"].to_list(),
        frames.assignments["member_id"].to_list(),
        strict=True,
    ):
        by_cid.setdefault(int(cid), []).append(int(mid))
    return frozenset(frozenset(members) for members in by_cid.values())
