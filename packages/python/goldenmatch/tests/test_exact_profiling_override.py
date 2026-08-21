"""Cluster-computed column statistics override the sampled ones (spec 2026-08-20).

`profile_columns` samples 1,000 rows, then runs an EXACT confirm pass over the
frame it was handed -- and stores it as `full_n_distinct`, documented as "the
EXACT full-frame count, never the sampled".

On the Spark path that label is false. `auto_configure_spark` pulls a 20,000-row
driver sample and hands THAT to `profile_columns`, so `full_n_distinct` is exact
over 20,000 rows out of a possibly 500M-row table -- and the drop-constant rule
reads it as though it described the population.

These tests are box-safe: polars only, no Spark. The cluster is represented by
the statistics it would return.
"""
from __future__ import annotations

import polars as pl


def test_full_n_distinct_comes_from_the_CLUSTER_not_the_local_frame():
    """The bug this slice exists for.

    A column that is constant in the 20k sample but has a rare second value in
    the full table must report the CLUSTER's count. Otherwise the drop-constant
    rule fires on a field that does discriminate, and it fires only at the
    scales where the sample stops covering the rare value.
    """
    from goldenmatch.core.autoconfig import (
        ExactStats,
        exact_column_stats_applied,
        profile_columns,
    )

    # Locally constant: every row says "active".
    frame = pl.DataFrame({
        "record_id": [str(i) for i in range(200)],
        "status": ["active"] * 200,
    })

    plain = {p.name: p for p in profile_columns(frame)}
    assert plain["status"].full_n_distinct == 1, "locally the column IS constant"

    # The cluster knows there are two values across 5M rows.
    stats = {"status": ExactStats(n_rows=5_000_000, n_non_null=5_000_000, n_distinct=2)}
    with exact_column_stats_applied(stats):
        got = {p.name: p for p in profile_columns(frame)}

    assert got["status"].full_n_distinct == 2, (
        "the cluster's count must win; otherwise a field that discriminates on "
        "rare pairs is dropped"
    )


def test_cardinality_ratio_uses_the_cluster_row_count():
    """The #876 shape: the ratio must be against the POPULATION, not the sample.

    An `email` column with a true fraction of 0.28 reads near 1.0 on any sample
    small enough, and every surrogate guard compares that to an absolute 1.0.
    """
    from goldenmatch.core.autoconfig import ExactStats, exact_column_stats_applied, profile_columns

    frame = pl.DataFrame({"email": [f"user{i}@example.com" for i in range(200)]})

    plain = {p.name: p for p in profile_columns(frame)}
    assert plain["email"].cardinality_ratio > 0.9, "locally every value is unique"

    stats = {"email": ExactStats(
        n_rows=5_000_000, n_non_null=5_000_000, n_distinct=1_400_000,
    )}
    with exact_column_stats_applied(stats):
        got = {p.name: p for p in profile_columns(frame)}

    assert abs(got["email"].cardinality_ratio - 0.28) < 0.01, (
        f"expected the true 0.28, got {got['email'].cardinality_ratio}"
    )


def test_the_override_is_scoped_and_leaves_no_residue():
    """A ContextVar that leaked would silently apply one run's cluster stats to
    the next caller's frame."""
    from goldenmatch.core.autoconfig import ExactStats, exact_column_stats_applied, profile_columns

    frame = pl.DataFrame({"status": ["active"] * 50})
    with exact_column_stats_applied({"status": ExactStats(n_rows=9, n_non_null=9, n_distinct=7)}):
        pass

    after = {p.name: p for p in profile_columns(frame)}
    assert after["status"].full_n_distinct == 1, "the override must not outlive its block"


def test_columns_the_cluster_did_not_measure_keep_their_local_values():
    """Absent is not zero, the same discipline as `candidates_counted`."""
    from goldenmatch.core.autoconfig import ExactStats, exact_column_stats_applied, profile_columns

    frame = pl.DataFrame({
        "status": ["active"] * 100,
        "city": [f"city{i % 7}" for i in range(100)],
    })
    stats = {"status": ExactStats(n_rows=5_000_000, n_non_null=5_000_000, n_distinct=2)}
    with exact_column_stats_applied(stats):
        got = {p.name: p for p in profile_columns(frame)}

    assert got["status"].full_n_distinct == 2
    assert got["city"].n_distinct == 7, "an unmeasured column keeps its local profile"
