"""S4 end-to-end gate: run_sail_pipeline runs on Sail and produces golden per
multi-member cluster. Skips where the sail extra is absent; runs in the `sail`
lane."""
from __future__ import annotations

import pytest

pytest.importorskip("pysail")
pytest.importorskip("pyspark")


@pytest.fixture(scope="module")
def spark():
    from pysail.spark import SparkConnectServer
    from pyspark.sql import SparkSession

    server = SparkConnectServer()
    server.start()
    _, port = server.listening_address
    sess = SparkSession.builder.remote(f"sc://localhost:{port}").getOrCreate()
    yield sess
    sess.stop()
    server.stop()


def test_run_sail_pipeline_end_to_end(spark):
    from goldenmatch.sail.pipeline import run_sail_pipeline

    rows = [
        (0, "10001", "Smith", "Jon"),
        (1, "10001", "Smith", None),     # cluster {0,1}: first_name "Jon"
        (2, "20002", "Brown", "Ann"),
        (3, "20002", "Brown", None),     # cluster {2,3}: first_name "Ann"
        (4, "30003", "Solo", "Zed"),     # singleton (excluded)
    ]
    df = spark.createDataFrame(
        rows, ["__row_id__", "zip", "last_name", "first_name"]
    )
    golden = run_sail_pipeline(
        df, id_col="__row_id__", block_col="zip", value_col="last_name",
        golden_cols=["first_name"], threshold=0.85, wcc="scale",
    )
    got = {int(r["cluster_id"]): r["first_name"] for r in golden.collect()}
    assert got == {0: "Jon", 2: "Ann"}
