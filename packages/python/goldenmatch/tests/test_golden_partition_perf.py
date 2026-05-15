"""Lock in the vectorized golden-record build.

Background: the per-cluster `collected_df.filter(...)` in the pipeline
was N*K work (re-scanning all rows for every cluster). The bench harness
surfaced it at 36% of wall on an 11K-row run. The fix filters once and
partitions by cluster_id.

These tests guard the rewrite from regressing:
  1. Output equivalence — every multi-member cluster still gets a
     golden record with the same set of column values as the unfiltered
     per-cluster path would have produced.
  2. Shape invariant — golden DataFrame still has one row per eligible
     cluster, no duplicates.
"""
from __future__ import annotations

import polars as pl
from goldenmatch import dedupe_df
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    GoldenRulesConfig,
    MatchkeyConfig,
    MatchkeyField,
)


def _cluster_df_with_dupes(n_clusters: int = 20, members_per_cluster: int = 3) -> pl.DataFrame:
    """Build a personlike df with `n_clusters` known clusters,
    each having `members_per_cluster` near-identical members so the
    dedupe pipeline emits multi-member clusters deterministically.
    """
    rows = []
    rid = 0
    for c in range(n_clusters):
        # Each cluster shares an email; rows differ slightly so we get
        # a realistic golden-merge surface.
        email = f"cluster{c}@example.com"
        for m in range(members_per_cluster):
            rows.append({
                "id": rid,
                "name": f"Person {c}" + ("." if m % 2 else ""),
                "email": email,
                "zip": f"100{c % 9:02d}",
            })
            rid += 1
    return pl.DataFrame(rows)


class TestGoldenPartitionOutput:
    def _config(self) -> GoldenMatchConfig:
        return GoldenMatchConfig(
            matchkeys=[
                MatchkeyConfig(
                    name="email_exact",
                    type="exact",
                    fields=[MatchkeyField(field="email", transforms=["lowercase"])],
                ),
            ],
            blocking=BlockingConfig(
                keys=[BlockingKeyConfig(fields=["zip"])],
                max_block_size=100,
                skip_oversized=False,
            ),
            golden_rules=GoldenRulesConfig(default_strategy="most_complete"),
        )

    def test_one_golden_per_multi_member_cluster(self):
        """The golden DataFrame must have exactly one row per multi-member cluster."""
        df = _cluster_df_with_dupes(n_clusters=15, members_per_cluster=3)
        result = dedupe_df(df, config=self._config())

        multi_member_count = sum(
            1 for c in result.clusters.values() if c.get("size", 0) > 1
        )
        # build_golden_record skips oversized; with max_block_size=100 and
        # cluster sizes of 3, nothing is oversized.
        assert result.golden is not None
        assert result.golden.height == multi_member_count
        assert result.golden.height == 15

    def test_no_duplicate_cluster_ids_in_golden(self):
        """The partitioned path must not emit two goldens for one cluster."""
        df = _cluster_df_with_dupes(n_clusters=25, members_per_cluster=4)
        result = dedupe_df(df, config=self._config())
        assert result.golden is not None
        cids = result.golden["__cluster_id__"].to_list()
        assert len(cids) == len(set(cids)), (
            f"Found duplicate cluster ids in golden output: {sorted(cids)}"
        )

    def test_golden_values_match_member_set(self):
        """Every golden row's column values must come from its cluster's members.

        The partitioned-vs-filtered split must not bleed values across
        clusters — a row in cluster A's golden record cannot have a
        value that only existed in cluster B's members.
        """
        df = _cluster_df_with_dupes(n_clusters=10, members_per_cluster=3)
        result = dedupe_df(df, config=self._config())
        assert result.golden is not None

        # Map cluster_id → set of zip values in its members.
        cluster_zip_sets: dict[int, set[str]] = {}
        for cid, info in result.clusters.items():
            if info.get("size", 0) <= 1:
                continue
            members = info["members"]
            member_zips = set(
                df["zip"].cast(pl.Utf8).to_list()[i] for i in members
            )
            cluster_zip_sets[cid] = member_zips

        # Every golden row's zip must be in its cluster's member-zip set.
        for row in result.golden.iter_rows(named=True):
            cid = row["__cluster_id__"]
            zip_val = str(row["zip"])
            assert zip_val in cluster_zip_sets[cid], (
                f"Golden cluster {cid}: zip {zip_val!r} not in member zips "
                f"{cluster_zip_sets[cid]}"
            )

    def test_oversized_clusters_excluded(self):
        """Oversized clusters must not appear in golden — matches prior behavior."""
        # Force an oversized cluster by making a giant cluster + tiny ones.
        rows = []
        for i in range(60):
            rows.append({"id": i, "name": "Big", "email": "big@example.com", "zip": "10001"})
        for c in range(3):
            for m in range(2):
                rows.append({
                    "id": 1000 + c * 2 + m,
                    "name": f"Small {c}",
                    "email": f"small{c}@example.com",
                    "zip": "10002",
                })
        df = pl.DataFrame(rows)
        cfg = GoldenMatchConfig(
            matchkeys=[
                MatchkeyConfig(
                    name="email_exact",
                    type="exact",
                    fields=[MatchkeyField(field="email", transforms=["lowercase"])],
                ),
            ],
            blocking=BlockingConfig(
                keys=[BlockingKeyConfig(fields=["zip"])],
                max_block_size=200,
                skip_oversized=False,
            ),
            golden_rules=GoldenRulesConfig(
                default_strategy="most_complete",
                max_cluster_size=20,  # smaller than the 60-member cluster
                auto_split=False,
            ),
        )
        result = dedupe_df(df, config=cfg)
        assert result.golden is not None
        oversized_cids = {
            cid for cid, info in result.clusters.items()
            if info.get("oversized")
        }
        golden_cids = set(result.golden["__cluster_id__"].to_list())
        assert oversized_cids.isdisjoint(golden_cids), (
            f"Oversized clusters {oversized_cids} leaked into golden {golden_cids}"
        )
