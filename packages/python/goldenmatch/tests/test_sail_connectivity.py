"""S1 de-risk: prove the Sail (Spark Connect) harness runs in CI before any
pipeline is built on it. Skips where the sail extra is absent; runs in the
`sail` CI lane (goldenmatch[sail] installed)."""
from __future__ import annotations

import pytest

pytest.importorskip("pysail")
pytest.importorskip("pyspark")


def test_sail_local_server_runs_trivial_query():
    from pysail.spark import SparkConnectServer
    from pyspark.sql import SparkSession

    server = SparkConnectServer()
    server.start()  # background
    try:
        _, port = server.listening_address
        spark = SparkSession.builder.remote(f"sc://localhost:{port}").getOrCreate()
        rows = spark.sql("SELECT 1 + 1 AS two").collect()
        assert rows[0]["two"] == 2
        spark.stop()
    finally:
        server.stop()
