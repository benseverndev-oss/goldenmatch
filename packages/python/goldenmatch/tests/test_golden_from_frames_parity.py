"""Phase 4 parity: build_golden_records_from_frames matches the
manual __cluster_id__-on-multi_df path.

GH issue #626 (Arrow-native roadmap Phase 4).

Asserts that ``build_golden_records_from_frames`` produces the SAME
golden records as building the ``multi_df`` manually and calling
``build_golden_records_batch`` directly. The new function is a thin
sibling that does the cluster-assignments join in a vectorized
Polars expression; the parity test locks the contract that the
implicit join produces the same shape as the explicit one.

Tests use tiny hand-built fixtures (≤ 20 rows). No ``dedupe_df``
calls.
"""
from __future__ import annotations

import polars as pl
from goldenmatch.config.schemas import GoldenRulesConfig
from goldenmatch.core.cluster import ClusterFrames
from goldenmatch.core.golden import (
    build_golden_records_batch,
    build_golden_records_from_frames,
)


def _cids_from_frames_result(result) -> list[int]:
    """Pull the __cluster_id__ set out of whichever slot the new
    tuple return populated (golden_df fast path or golden_records slow
    path)."""
    golden_df, golden_records = result
    if golden_df is not None:
        return sorted(golden_df["__cluster_id__"].to_list())
    return sorted(r["__cluster_id__"] for r in golden_records)


def _make_source() -> pl.DataFrame:
    """Tiny 6-row people frame; 2 multi-member clusters + 1 singleton."""
    return pl.DataFrame({
        "__row_id__": [1, 2, 3, 4, 5, 6],
        "first_name": ["John", "Jon", "John", "Bob", "Bobby", "Alice"],
        "last_name": ["Smith", "Smith", "Smyth", "Brown", "Brown", "Cooper"],
        "zip": ["10001", "10001", "10001", "20002", "20002", "30003"],
    })


def _make_frames() -> ClusterFrames:
    """ClusterFrames for the _make_source() shape.

    cluster 100: members 1, 2, 3 (three Smith variants)
    cluster 101: members 4, 5    (two Brown variants)
    cluster 102: member  6       (Alice, singleton)
    """
    assignments = pl.DataFrame({
        "cluster_id": pl.Series([100, 100, 100, 101, 101, 102], dtype=pl.Int64),
        "member_id":  pl.Series([1, 2, 3, 4, 5, 6], dtype=pl.Int64),
    })
    metadata = pl.DataFrame({
        "cluster_id": pl.Series([100, 101, 102], dtype=pl.Int64),
        "size":       pl.Series([3, 2, 1], dtype=pl.Int64),
        "confidence": pl.Series([0.95, 0.88, 1.0], dtype=pl.Float64),
        "quality":    pl.Series(["strong", "strong", "strong"], dtype=pl.Utf8),
        "oversized":  pl.Series([False, False, False], dtype=pl.Boolean),
        "bottleneck_pair_a": pl.Series([1, 4, 0], dtype=pl.Int64),
        "bottleneck_pair_b": pl.Series([3, 5, 0], dtype=pl.Int64),
    })
    return ClusterFrames(assignments=assignments, metadata=metadata)


def _manual_multi_df(source: pl.DataFrame, frames: ClusterFrames) -> pl.DataFrame:
    """The explicit join that callers would otherwise write."""
    multi_cluster_ids = (
        frames.metadata.filter(pl.col("size") > 1).select("cluster_id")
    )
    assignments_multi = frames.assignments.join(
        multi_cluster_ids, on="cluster_id", how="inner",
    ).rename({"cluster_id": "__cluster_id__"})
    return source.join(
        assignments_multi,
        left_on="__row_id__",
        right_on="member_id",
        how="inner",
    )


class TestGoldenFromFramesParity:
    def test_records_match_manual_join(self):
        source = _make_source()
        frames = _make_frames()
        rules = GoldenRulesConfig(default_strategy="most_complete")

        new = build_golden_records_from_frames(source, frames, rules)
        old = build_golden_records_batch(
            _manual_multi_df(source, frames), rules,
        )

        new_cids = _cids_from_frames_result(new)
        old_cids = sorted(r["__cluster_id__"] for r in old)
        # Same number of golden records (one per multi-member cluster)
        assert len(new_cids) == len(old_cids), (
            f"record count differs: new={len(new_cids)}, old={len(old_cids)}"
        )
        # Same set of __cluster_id__ values
        assert new_cids == old_cids, (
            f"cluster ids differ: new={new_cids}, old={old_cids}"
        )

    def test_singleton_clusters_dropped(self):
        """The metadata.size > 1 filter must drop singleton clusters
        (cluster 102 / Alice in this fixture)."""
        source = _make_source()
        frames = _make_frames()
        rules = GoldenRulesConfig(default_strategy="most_complete")

        new = build_golden_records_from_frames(source, frames, rules)
        cids = set(_cids_from_frames_result(new))

        assert 100 in cids
        assert 101 in cids
        assert 102 not in cids, (
            "singleton cluster 102 leaked into golden output; "
            "metadata.size > 1 filter is broken"
        )

    def test_empty_source(self):
        empty_source = pl.DataFrame({
            "__row_id__": pl.Series([], dtype=pl.Int64),
            "first_name": pl.Series([], dtype=pl.Utf8),
        })
        frames = _make_frames()
        rules = GoldenRulesConfig(default_strategy="most_complete")
        assert build_golden_records_from_frames(empty_source, frames, rules) == (
            None, [],
        )

    def test_empty_assignments(self):
        source = _make_source()
        empty_frames = ClusterFrames(
            assignments=pl.DataFrame({
                "cluster_id": pl.Series([], dtype=pl.Int64),
                "member_id":  pl.Series([], dtype=pl.Int64),
            }),
            metadata=pl.DataFrame({
                "cluster_id": pl.Series([], dtype=pl.Int64),
                "size":       pl.Series([], dtype=pl.Int64),
                "confidence": pl.Series([], dtype=pl.Float64),
                "quality":    pl.Series([], dtype=pl.Utf8),
                "oversized":  pl.Series([], dtype=pl.Boolean),
                "bottleneck_pair_a": pl.Series([], dtype=pl.Int64),
                "bottleneck_pair_b": pl.Series([], dtype=pl.Int64),
            }),
        )
        rules = GoldenRulesConfig(default_strategy="most_complete")
        assert build_golden_records_from_frames(source, empty_frames, rules) == (
            None, [],
        )

    def test_missing_row_id_raises(self):
        source_no_rowid = pl.DataFrame({
            "first_name": ["John"],
            "last_name": ["Smith"],
        })
        frames = _make_frames()
        rules = GoldenRulesConfig(default_strategy="most_complete")
        try:
            build_golden_records_from_frames(source_no_rowid, frames, rules)
        except ValueError as e:
            assert "__row_id__" in str(e)
        else:
            raise AssertionError("expected ValueError for missing __row_id__")

    def test_member_id_not_in_source_silently_dropped(self):
        """If assignments references a member_id that isn't in
        source_df, that cluster is silently dropped (consistent with
        the existing pipeline)."""
        source = _make_source().head(3)  # only rows 1, 2, 3
        # Frames reference 1-6; only 100 has all members in source
        frames = _make_frames()
        rules = GoldenRulesConfig(default_strategy="most_complete")

        new = build_golden_records_from_frames(source, frames, rules)
        cids = set(_cids_from_frames_result(new))

        # Cluster 100 (1, 2, 3) is fully present -> included
        assert 100 in cids
        # Cluster 101 (4, 5) has no members in source -> empty multi_df
        # for that group -> dropped by the join
        assert 101 not in cids
