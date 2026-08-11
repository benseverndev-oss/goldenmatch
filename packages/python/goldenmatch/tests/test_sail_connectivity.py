"""S1 de-risk: prove the Spark Connect harness runs in CI before any pipeline
is built on it. Backend-agnostic (P0): the ``spark`` fixture picks pysail or a
real Spark Connect server from ``GOLDENMATCH_SPARK_REMOTE``, so this same gate
proves either one. Skips without a Spark client."""
from __future__ import annotations

import pytest

pytest.importorskip("pyspark")


def test_spark_connect_runs_trivial_query(spark):
    rows = spark.sql("SELECT 1 + 1 AS two").collect()
    assert rows[0]["two"] == 2
