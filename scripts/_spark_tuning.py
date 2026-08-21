#!/usr/bin/env python3
"""Spark settings applied IDENTICALLY to both arms of the comparison.

## Why this is a shared module and not two copies

Splink's performance guide recommends `spark.sql.shuffle.partitions` at roughly
5x the cluster's CPU count. Both harnesses previously ran on Spark's default
200, which is a number neither vendor recommends -- fair in the sense that
neither was tuned, and wrong in the sense that it is not how Splink says to run
at scale.

The fix is to follow the guidance for BOTH engines. That only stays true if the
setting is applied from one implementation: two copies of "the same" tuning
drift, and a benchmark whose arms are tuned differently measures the tuning.
Same reasoning as `_fs_quality_metrics.py` being shared -- a difference between
the arms' numbers should be a difference between the ENGINES.

## Why only `spark.sql.shuffle.partitions`

The guide also names `spark.default.parallelism`. That one is a static core
config, not settable at runtime on a Spark CONNECT session -- which is how
GoldenMatch connects and Splink cannot. Setting it for Splink alone would hand
one arm a knob the other cannot reach, which is exactly the asymmetry this
module exists to prevent. It is left at Spark's default for both, and that is
recorded rather than quietly omitted.

`spark.sql.shuffle.partitions` IS a runtime SQL conf, so it applies identically
through Connect and through a classic session.
"""

from __future__ import annotations


def executor_cores_from_ui(spark_ui: str | None) -> int | None:
    """Total executor cores as the CLUSTER reports them, or None.

    Read from the Spark UI's executor list rather than derived from the
    workflow's `workers x machine cores`: the derived figure is what we ASKED
    for, and Splink's guidance is about the cores the job actually has. If an
    executor failed to register, deriving would silently over-shard.

    The driver appears in that list with `total_cores` 0, so summing is safe.
    Returns None on any failure -- an absent measurement must leave Spark's
    default in place rather than produce a fabricated setting.
    """
    if not spark_ui:
        return None
    try:
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parent / "_spark_shuffle_metrics.py"
        spec = importlib.util.spec_from_file_location("_spark_shuffle_metrics", path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        info = mod.fetch(spark_ui)
        rows = (info.get("executor_metrics") or {}).get("executors") or []
        total = sum(r.get("total_cores") or 0 for r in rows)
        return total or None
    except Exception:
        return None


def resolve_shuffle_partitions(requested: int, executor_cores: int | None) -> int | None:
    """The partition count to use, or None to leave Spark's default alone.

    ``requested`` of 0 means "leave it alone"; a negative value means "derive it
    from the cluster" as 5x total executor cores, per Splink's guide. Returns
    None when it cannot be derived, so the caller leaves the default rather than
    inventing a number -- an absent input must not become a fabricated setting.
    """
    if requested > 0:
        return requested
    if requested < 0 and executor_cores and executor_cores > 0:
        return 5 * executor_cores
    return None


def apply_shuffle_partitions(
    spark, requested: int, spark_ui: str | None = None, executor_cores: int | None = None
) -> int | None:
    """Set `spark.sql.shuffle.partitions`, and report what was actually applied.

    Returns the value set, or None if left at Spark's default. The return value
    is written into both harnesses' JSON so an artifact can never be read
    without knowing which partition count produced it.
    """
    if executor_cores is None:
        executor_cores = executor_cores_from_ui(spark_ui)
    n = resolve_shuffle_partitions(requested, executor_cores)
    if n is None:
        try:
            current = spark.conf.get("spark.sql.shuffle.partitions")
        except Exception:  # pragma: no cover - diagnostic only
            current = "unknown"
        print(f"[tuning] shuffle.partitions left at Spark's default ({current})", flush=True)
        return None
    spark.conf.set("spark.sql.shuffle.partitions", str(n))
    print(f"[tuning] shuffle.partitions = {n}", flush=True)
    return n
