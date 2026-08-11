"""Ship the client's Python environment to Spark executors (P1).

Spark Connect's ``addArtifact(..., archive=True)`` uploads an archive and unpacks
it executor-side; ``spark.sql.execution.pyspark.python`` then points the UDF
worker at that interpreter. Together they put ``goldenmatch`` on the executors
**without a cluster-side install**, which is the whole zero-friction claim for a
Splink-on-Spark cutover: no platform-team ticket, no cluster libraries, no image
rebuild.

**Spark Connect only.** ``addArtifact`` raises on a classic session; that is a
deliberate constraint of the design, not an oversight (spec
``2026-08-10-spark-native-execution-design`` §4).

Why this module exists at all: P0 (run 31496638072) found the tier is fully
Spark-Connect-compatible -- 36 tests pass, no API gaps -- and that all 20
failures were ``ModuleNotFoundError`` inside the Python UDF worker. The tier
works; its dependencies were simply never delivered. Under pysail this could not
surface, because pysail's Connect server is in-process and its worker shares the
client interpreter.
"""
from __future__ import annotations

import json
from typing import Any

_ENV_NAME = "environment"


def ship_python_environment(
    spark: Any, archive: str, env_name: str = _ENV_NAME
) -> None:
    """Upload ``archive`` and point the UDF workers at its interpreter.

    ``archive`` is a relocatable virtualenv -- e.g. built with ``venv-pack``,
    ``conda-pack`` or PEX -- containing ``goldenmatch`` and its runtime deps.

    **Platform trap.** The archive must be built for the EXECUTOR platform
    (manylinux), not the client's. A venv packed on macOS or Windows will upload
    and unpack happily, then fail to execute on Linux executors. Worse, the
    scorer falls back to pure rapidfuzz on any error, so a mismatched archive can
    look like success while delivering none of the native speed. Build it on the
    target platform (CI is the obvious place).

    Args:
        spark: an active Spark **Connect** session.
        archive: path to the packed environment.
        env_name: unpack directory name on the executor; also the interpreter
            path prefix. Rarely needs changing.
    """
    spark.addArtifact(f"{archive}#{env_name}", archive=True)
    spark.conf.set("spark.sql.execution.pyspark.python", f"./{env_name}/bin/python")


def executor_probe(spark: Any) -> dict[str, Any]:
    """Report what is importable **on the executor**. Never raises.

    Deliberately implemented as a UDF: a driver-side check proves nothing,
    because the driver is where the client venv already lives. A UDF body only
    executes in a Python worker, so reaching the probe at all establishes that we
    are off the driver.

    Returns a dict with ``ran_on``, ``goldenmatch``, ``rapidfuzz``, ``pandas``,
    ``pyarrow``, ``native_kernel`` and ``executable``.

    ``native_kernel: False`` is **not** a dependency failure on its own --
    ``sail_scoring`` is in ``_FALLBACK_ONLY`` (f32 vs the f64 floor) and does not
    run under ``auto`` regardless. Lifting that is P3.
    """
    from pyspark.sql.functions import udf
    from pyspark.sql.types import StringType

    @udf(returnType=StringType())
    def _probe(_ignored: str) -> str:
        import importlib.util
        import json as _json
        import os as _os
        import sys as _sys

        def _importable(name: str) -> bool:
            try:
                return importlib.util.find_spec(name) is not None
            except Exception:  # noqa: BLE001 - a probe must never raise
                return False

        native = False
        try:
            from goldenmatch.core._native_loader import native_module

            native = native_module() is not None
        except Exception:  # noqa: BLE001 - absent kernel is a datum, not an error
            native = False

        return _json.dumps(
            {
                "ran_on": "executor",
                "goldenmatch": _importable("goldenmatch"),
                "rapidfuzz": _importable("rapidfuzz"),
                "pandas": _importable("pandas"),
                "pyarrow": _importable("pyarrow"),
                "native_kernel": native,
                "executable": _sys.executable,
                "pyspark_python": _os.environ.get("PYSPARK_PYTHON", "?"),
            }
        )

    row = (
        spark.range(1)
        .selectExpr("cast(id as string) as s")
        .select(_probe("s").alias("report"))
        .collect()[0]
    )
    return json.loads(row["report"])
