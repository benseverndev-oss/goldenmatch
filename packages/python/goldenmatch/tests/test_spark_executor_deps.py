"""P1: the executor's Python worker can import goldenmatch, because the client
shipped it at session time.

Without this every ``pandas_udf`` dies with ``ModuleNotFoundError`` -- which is
exactly what P0 measured (run 31496638072: 20 failures, one cause). These tests
run ON THE EXECUTOR; asking the driver would prove nothing, because the driver
is where the client venv already lives.

Skips unless a real Spark backend is selected: under pysail the Connect server is
in-process and its worker shares the client interpreter, so the imports trivially
succeed and the test would be vacuous.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("pyspark")

_REAL_SPARK = bool(os.environ.get("GOLDENMATCH_SPARK_REMOTE"))
_needs_real_spark = pytest.mark.skipif(
    not _REAL_SPARK,
    reason=(
        "pysail's worker shares the client interpreter, so this proves nothing "
        "there; set GOLDENMATCH_SPARK_REMOTE to exercise a forked worker"
    ),
)


@_needs_real_spark
def test_executor_can_import_goldenmatch(spark):
    """The P1 proof."""
    from goldenmatch.sail.deps import executor_probe

    report = executor_probe(spark)
    assert report["goldenmatch"] is True, (
        f"goldenmatch is not importable on the executor: {report}"
    )
    # The scorer floor is goldenmatch's OWN strsim. rapidfuzz is a dev-only
    # extra -- asserting it here would force shipping a dependency the product
    # deliberately removed (it was replaced by owned bit-parallel strsim).
    assert report["strsim"] is True, f"goldenmatch.core.strsim missing: {report}"
    assert report["pandas"] is True, (
        f"pandas missing on the executor -- pandas_udf cannot run: {report}"
    )


@_needs_real_spark
def test_probe_actually_ran_on_the_executor(spark):
    """Guard on the guard: a probe that silently reported the DRIVER's
    interpreter would pass while proving nothing at all."""
    from goldenmatch.sail.deps import executor_probe

    report = executor_probe(spark)
    assert report["ran_on"] == "executor", report


@_needs_real_spark
def test_native_kernel_presence_is_reported_not_asserted(spark):
    """The native kernel being ABSENT on the executor is not a P1 failure.

    ``sail_scoring`` is in ``_FALLBACK_ONLY`` (f32 vs the f64 floor), so it does
    not run under ``auto`` regardless -- lifting that is P3's job. P1 only owes
    you the dependency delivery. This test pins the distinction so a `false` here
    is never misread as a P1 regression.
    """
    from goldenmatch.sail.deps import executor_probe

    report = executor_probe(spark)
    assert "native_kernel" in report, report
    assert isinstance(report["native_kernel"], bool), report
