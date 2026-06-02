"""SP-B Task 1: build_golden_records_from_frames must EXCLUDE oversized
clusters, matching the dict pipeline's golden selection
(``info["size"] > 1 and not info["oversized"]`` in pipeline.py ~:1528).

Builds clusters with ``auto_split=False`` so an over-cap cluster stays
flagged ``oversized`` instead of being split, converts to
``ClusterFrames`` via ``cluster_dict_to_frames``, and asserts the
from-frames golden builder drops it.

Tiny hand-built fixtures, no ``dedupe_df`` calls.
"""
from __future__ import annotations

import polars as pl
from goldenmatch.config.schemas import GoldenRulesConfig
from goldenmatch.core.cluster import build_clusters, cluster_dict_to_frames
from goldenmatch.core.golden import (
    build_golden_records_batch,
    build_golden_records_from_frames,
)


def _src(ids: list[int]) -> pl.DataFrame:
    """source_df with a __row_id__ col covering the member ids + a value col."""
    return pl.DataFrame({
        "__row_id__": pl.Series(ids, dtype=pl.Int64),
        "name": [f"n{i}" for i in ids],
    })


def _cids_from_result(result) -> set[int]:
    """__cluster_id__ set out of whichever tuple slot is populated."""
    golden_df, golden_records = result
    if golden_df is not None:
        return set(golden_df["__cluster_id__"].to_list())
    return {r["__cluster_id__"] for r in golden_records}


def _build() -> tuple[dict, pl.DataFrame]:
    # {0,1,2,3} is a size-4 clique (> max_cluster_size=3 -> oversized);
    # {10,11} is a size-2 cluster (eligible).
    pairs = [
        (0, 1, 0.9), (1, 2, 0.9), (0, 2, 0.9),
        (2, 3, 0.9), (0, 3, 0.9), (1, 3, 0.9),
        (10, 11, 0.9),
    ]
    clusters = build_clusters(
        pairs,
        all_ids=[0, 1, 2, 3, 10, 11],
        max_cluster_size=3,
        auto_split=False,
    )
    src = _src([0, 1, 2, 3, 10, 11])
    return clusters, src


class TestFromFramesExcludesOversized:
    def test_oversized_cluster_excluded(self):
        clusters, src = _build()

        # Sanity: there IS an oversized multi-member cluster in the dict.
        oversized = [
            cid for cid, c in clusters.items()
            if c["size"] > 1 and c["oversized"]
        ]
        assert oversized, "fixture did not produce an oversized cluster"

        frames = cluster_dict_to_frames(clusters)
        rules = GoldenRulesConfig(default_strategy="most_complete")

        got = build_golden_records_from_frames(src, frames, rules)
        got_cids = _cids_from_result(got)

        # Reference: dict path's selection (size > 1 AND not oversized).
        ref_dict = {
            cid: c for cid, c in clusters.items()
            if c["size"] > 1 and not c["oversized"]
        }
        # Attach __cluster_id__ to multi_df the way the dict pipeline does.
        members_to_cid: dict[int, int] = {}
        for cid, c in ref_dict.items():
            for m in c["members"]:
                members_to_cid[m] = cid
        ref_multi = (
            src.filter(pl.col("__row_id__").is_in(list(members_to_cid)))
            .with_columns(
                pl.col("__row_id__")
                .replace_strict(
                    list(members_to_cid.keys()),
                    list(members_to_cid.values()),
                    return_dtype=pl.Int64,
                )
                .alias("__cluster_id__")
            )
        )
        ref = build_golden_records_batch(ref_multi, rules)
        ref_cids = {r["__cluster_id__"] for r in ref}

        assert got_cids == ref_cids, (
            f"from-frames cluster ids {got_cids} != dict-path ids {ref_cids}"
        )
        # The oversized cluster id must NOT appear in the golden output.
        for cid in oversized:
            assert cid not in got_cids, (
                f"oversized cluster {cid} leaked into golden output"
            )
