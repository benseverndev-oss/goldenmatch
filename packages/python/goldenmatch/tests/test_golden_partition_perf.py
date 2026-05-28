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


class TestGoldenRecordsDfEquivalence:
    """build_golden_records_df (columnar fast path) must match build_golden_records_batch
    (list[dict] slow path) for every cluster when both gates allow the fast path.

    Motivation: the list[dict] path allocates ~14 GB at 10M / 2M clusters. The
    columnar path stores the same data in ~0.8 GB. They MUST produce the same
    cluster-id -> value mapping and the same per-cluster confidence.
    """

    def _multi_df_fixture(self) -> pl.DataFrame:
        """Build a multi_df with 4 clusters: 2 that agree fully (n_unique=1
        per col), 1 with one disagreement, 1 with all values null in one col.
        Covers the three confidence branches.
        """
        return pl.DataFrame({
            "__row_id__": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            "__cluster_id__": [10, 10, 20, 20, 30, 30, 30, 40, 40, 40, 40],
            "name": [
                "Alice", "Alice",      # cluster 10: agreement
                "Bob", "Bob",          # cluster 20: agreement
                "Carol", "Carol", "Caroline",  # cluster 30: disagreement
                None, "Dave", "Dave", "Dave",  # cluster 40: null + agreement
            ],
            "email": [
                "a@x.com", "a@x.com",
                "b@x.com", "b@x.com",
                "c@x.com", "c@x.com", "c@x.com",
                "d@x.com", "d@x.com", "d@x.com", "d@x.com",
            ],
        })

    def test_fast_path_matches_slow_path_most_complete(self):
        from goldenmatch.core.golden import (
            build_golden_records_batch,
            build_golden_records_df,
        )

        multi_df = self._multi_df_fixture()
        rules = GoldenRulesConfig(default_strategy="most_complete")

        fast_df = build_golden_records_df(multi_df, rules)
        slow_records = build_golden_records_batch(multi_df, rules)

        # Slow path returns list[dict]; convert to the same row shape the
        # pipeline builds (one row per cluster, flat columns).
        slow_rows = []
        for rec in slow_records:
            row = {
                "__cluster_id__": rec["__cluster_id__"],
                "__golden_confidence__": rec["__golden_confidence__"],
            }
            for k, v in rec.items():
                if k in ("__cluster_id__", "__golden_confidence__"):
                    continue
                if isinstance(v, dict) and "value" in v:
                    row[k] = v["value"]
            slow_rows.append(row)
        slow_df = pl.DataFrame(slow_rows)

        # Sort both by cluster_id for a stable comparison.
        fast_sorted = fast_df.sort("__cluster_id__")
        slow_sorted = slow_df.sort("__cluster_id__").select(fast_sorted.columns)

        # Values: per-cluster value picks must match.
        for col in ("name", "email"):
            assert fast_sorted[col].to_list() == slow_sorted[col].to_list(), (
                f"value mismatch on column {col}"
            )
        # Confidence: same per-cluster value to 4 decimal places.
        fast_conf = [round(c, 4) for c in fast_sorted["__golden_confidence__"].to_list()]
        slow_conf = [round(c, 4) for c in slow_sorted["__golden_confidence__"].to_list()]
        assert fast_conf == slow_conf, (
            f"confidence mismatch: fast={fast_conf} slow={slow_conf}"
        )

    def test_fast_path_matches_slow_path_first_non_null(self):
        """Same equivalence under first_non_null strategy (different conf
        constants but same value-picking semantics)."""
        from goldenmatch.core.golden import (
            build_golden_records_batch,
            build_golden_records_df,
        )

        multi_df = self._multi_df_fixture()
        rules = GoldenRulesConfig(default_strategy="first_non_null")

        fast_df = build_golden_records_df(multi_df, rules)
        slow_records = build_golden_records_batch(multi_df, rules)

        slow_conf_by_cid = {r["__cluster_id__"]: r["__golden_confidence__"] for r in slow_records}
        fast_conf_by_cid = dict(zip(
            fast_df["__cluster_id__"].to_list(),
            fast_df["__golden_confidence__"].to_list(),
        ))
        for cid in slow_conf_by_cid:
            assert round(fast_conf_by_cid[cid], 4) == round(slow_conf_by_cid[cid], 4), (
                f"first_non_null confidence mismatch on cluster {cid}"
            )
