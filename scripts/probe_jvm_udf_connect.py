"""PROBE: can a Java UDF be registered and called from a Spark CONNECT client?

This decides the shape of the JVM binding, not whether to build it.

Spark Connect deliberately does not expose Catalyst, so a custom `Expression`
(the fastest option, with codegen) cannot be registered from a Connect client.
What might still work is a plain Java UDF: `addArtifact` delivers per-session
jars with their own classloader, and PySpark exposes `registerJavaFunction`.

    If a path works  -> the JVM binding is a DROP-IN. The jar ships per session
                        exactly as P1 ships the Python env, and the customer's
                        cluster needs nothing installed.
    If none works    -> the binding requires a jar on the cluster classpath,
                        which reinstates the friction P0-P6 removed. That is a
                        different product decision, not an optimization.

Each path is probed independently and its exact exception recorded, because
"it failed" is not an answer -- `AnalysisException: cannot resolve` and
`PySparkNotImplementedError` mean opposite things about whether to keep trying.

RESULT (run 31609185942, 2026-08-12; pyspark 4.2.0, Temurin 17, remote=local[*])

    PASS  addArtifact(jar)                  jar accepted by the session
    PASS  spark.udf.registerJavaFunction    registered AND called -> [1.0, 0.0]
    FAIL  CREATE TEMPORARY FUNCTION ... AS  AnalysisException [NO_HANDLER_FOR_UDAF]:
          "No handler for UDAF '<class>'. Use sparkSession.udf.register(...) instead."
    PASS  CONTROL: python udf               [1.0, 0.0]

=> A Java UDF IS registrable over Spark Connect. The JVM binding can be a
   DROP-IN: ship the jar per session via addArtifact, exactly as P1 ships the
   Python env, and the customer's cluster needs NOTHING installed.

The one failure is not a Connect restriction: the SQL DDL path resolves the
class as a UDAF and says so, pointing at the API that works. Recorded rather
than dropped, because "the SQL path does not work" is a thing a future reader
will otherwise rediscover -- and because if it ever starts working, a scorer
becomes callable from plain SQL, which is a different and useful surface.

The CONTROL passing is what makes the FAIL trustworthy: the session was healthy,
so that row is a real capability answer rather than a broken run.

Re-run this before assuming the answer still holds on a new Spark version -- the
`pyspark_spec` input exists for exactly that.

Run in CI (never locally -- a Spark install is not something to put on a laptop):
    .github/workflows/probe-jvm-udf-connect.yml
"""
from __future__ import annotations

import os
import sys
import traceback

CLASS_NAME = "dev.goldensuite.probe.GoldenScoreProbeUdf"
JAR_ENV = "GOLDENMATCH_PROBE_JAR"

_results: list[tuple[str, bool, str]] = []


def _record(path: str, ok: bool, detail: str) -> None:
    _results.append((path, ok, detail))
    print(f"\n[{'PASS' if ok else 'FAIL'}] {path}\n      {detail}", flush=True)


def _exc(e: BaseException) -> str:
    """One-line summary plus the exception TYPE, which is the load-bearing part:
    a NotImplementedError means Connect refuses the API, an AnalysisException
    means it accepted the call and could not resolve the class."""
    first = str(e).strip().splitlines()
    return f"{type(e).__name__}: {first[0] if first else '(no message)'}"


def main() -> int:
    jar = os.environ.get(JAR_ENV)
    if not jar or not os.path.exists(jar):
        print(f"::error::{JAR_ENV} not set or missing: {jar!r}")
        return 2

    from pyspark.sql import SparkSession

    remote = os.environ.get("GOLDENMATCH_SPARK_REMOTE", "local[*]")
    spark = SparkSession.builder.remote(remote).getOrCreate()
    print(f"pyspark {__import__('pyspark').__version__} against remote={remote!r}")
    print(f"probe jar: {jar}")

    df = spark.createDataFrame([("a", "a"), ("a", "b")], ["x", "y"])

    # ── Step 1: can the jar even be delivered? ───────────────────────
    try:
        spark.addArtifact(jar, pyfile=False, archive=False, file=False)
        _record("addArtifact(jar)", True, "jar accepted by the session")
    except TypeError:
        # Older signature: addArtifact(path) with no kwargs.
        try:
            spark.addArtifact(jar)
            _record("addArtifact(jar)", True, "jar accepted (positional form)")
        except Exception as e:  # noqa: BLE001 - reporting, not handling
            _record("addArtifact(jar)", False, _exc(e))
    except Exception as e:  # noqa: BLE001
        _record("addArtifact(jar)", False, _exc(e))

    # ── Step 2a: the documented PySpark path ─────────────────────────
    try:
        spark.udf.registerJavaFunction("probe_udf_a", CLASS_NAME, "double")
        out = df.selectExpr("probe_udf_a(x, y) AS s").collect()
        got = [r["s"] for r in out]
        ok = got == [1.0, 0.0]
        _record(
            "spark.udf.registerJavaFunction",
            ok,
            f"registered and called; got {got} (expected [1.0, 0.0])",
        )
    except Exception as e:  # noqa: BLE001
        _record("spark.udf.registerJavaFunction", False, _exc(e))

    # ── Step 2b: the SQL DDL path ────────────────────────────────────
    # Worth probing separately: it goes through the SQL parser rather than the
    # Connect UDF proto, so it can succeed where 2a fails (or vice versa).
    try:
        spark.sql(
            f"CREATE OR REPLACE TEMPORARY FUNCTION probe_udf_b AS '{CLASS_NAME}'"
        )
        out = df.selectExpr("probe_udf_b(x, y) AS s").collect()
        got = [r["s"] for r in out]
        ok = got == [1.0, 0.0]
        _record(
            "CREATE TEMPORARY FUNCTION ... AS",
            ok,
            f"registered and called; got {got} (expected [1.0, 0.0])",
        )
    except Exception as e:  # noqa: BLE001
        _record("CREATE TEMPORARY FUNCTION ... AS", False, _exc(e))

    # ── Step 3: control ──────────────────────────────────────────────
    # A Python UDF must work. If it does not, the session is broken and every
    # FAIL above is meaningless -- the probe would otherwise report a confident
    # "Connect cannot do this" when the truth is "nothing ran at all".
    try:
        from pyspark.sql.functions import col, udf

        py_udf = udf(lambda a, b: 1.0 if a == b else 0.0, "double")
        got = [r["s"] for r in df.select(udf_alias(py_udf, col("x"), col("y"))).collect()]
        _record("CONTROL: python udf", got == [1.0, 0.0], f"got {got}")
    except Exception as e:  # noqa: BLE001
        _record("CONTROL: python udf", False, _exc(e))

    # ── Verdict ──────────────────────────────────────────────────────
    print("\n" + "=" * 68)
    print("VERDICT")
    print("=" * 68)
    for path, ok, detail in _results:
        print(f"  {'PASS' if ok else 'FAIL'}  {path}")
        print(f"        {detail}")

    control_ok = any(p.startswith("CONTROL") and ok for p, ok, _ in _results)
    java_ok = any(
        ok for p, ok, _ in _results if p in
        ("spark.udf.registerJavaFunction", "CREATE TEMPORARY FUNCTION ... AS")
    )

    print()
    if not control_ok:
        print("INCONCLUSIVE: the control failed, so the session itself is broken.")
        print("Every result above says nothing about Spark Connect's capabilities.")
        return 3
    if java_ok:
        print("A Java UDF IS registrable over Spark Connect.")
        print("=> the JVM binding can be a DROP-IN: ship the jar per session via")
        print("   addArtifact, exactly as P1 ships the Python env. The customer's")
        print("   cluster needs nothing installed.")
        return 0
    print("A Java UDF is NOT registrable over Spark Connect by any probed path.")
    print("=> the JVM binding needs the jar on the CLUSTER classpath. That")
    print("   reinstates the install friction P0-P6 removed, and is a product")
    print("   decision rather than an optimization. Read the exact exception")
    print("   types above before concluding it is impossible.")
    return 1


def udf_alias(fn, a, b):
    """`fn(a, b).alias("s")` -- extracted only so the call site above stays one
    line and readable."""
    return fn(a, b).alias("s")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - a crash here is itself a finding
        traceback.print_exc()
        print("\n::error::probe crashed before reaching a verdict")
        sys.exit(4)
