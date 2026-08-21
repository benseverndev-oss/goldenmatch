"""Shuffle BYTES per stage, from Spark's REST API.

## Why bytes and not seconds

The open question on the Spark tier is whether GoldenMatch's advantage survives
a real multi-node cluster. The rig cannot answer it: both workers are containers
on ONE host, so a shuffle never crosses a network, and a wall measured there
says nothing about one that does.

Bytes do transfer. Network cost on ANY topology is a function of what crosses
the exchange, so measuring that answers the question without needing the
network -- and without the overlay-network hack, whose own DERP relay fallback
would make a latency measurement meaningless in a way the output would not show.

The prediction this tests: GoldenMatch's counting stage is a `GROUP BY` over
agreement patterns whose output is bounded by `prod(levels + 1)`, and Spark
combines map-side, so what crosses should be ~`partitions x distinct patterns`
regardless of pair count -- kilobytes. Splink re-scans pairs per EM iteration,
so its shuffle should scale with pairs and repeat ~26 times. If that holds, the
advantage widens with cluster size rather than narrowing, and the claim needs no
multi-node wall to stand.

## Why the parsing is a separate function

`summarize()` takes the decoded `/stages` payload and is unit-testable; only
`fetch()` touches the network. A metric that silently reports zero because a
field was renamed is the failure mode here, so `summarize` distinguishes "no
shuffle" from "no stages" and says which.

## Everything else the endpoints carry

The original version kept three fields per stage and discarded the rest, which
was right for the bytes-only question above. A 50M head-to-head asks more: when
one engine is slower, is it CPU, GC, spill, or skew? So `summarize` now keeps
the whole stage record (executor run/CPU time, GC, memory and disk spill, peak
execution memory, input/output bytes and records), and `summarize_executors`
reads `/executors` for per-executor heap, disk, task counts and liveness.

Per-executor rows are kept rather than only their totals because at scale the
likely failure is disk or memory on ONE node, and a cluster-wide sum hides
exactly that. `any_executor_died` is surfaced at the top level for the same
reason.

Every added field follows the module's existing rule, now applied per-field:
`_int_or_none` / `_sum_or_none` return None when nothing reported the metric,
so "Spark did not collect this" never renders as a measured zero. `sum()` over
an all-empty list is 0, which would read as "no GC time" instead of "unknown" --
that is the same absent-vs-zero collapse the engine has hit repeatedly, and it
is just as easy to write here.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


def summarize(stages: list[dict[str, Any]], top_n: int = 5) -> dict[str, Any]:
    """Totals plus the heaviest stages, from a Spark `/stages` payload.

    `stages` is the decoded list. Keys are Spark's own
    (`shuffleWriteBytes` / `shuffleReadBytes`); a payload missing BOTH on every
    stage yields `fields_present: False` rather than a confident zero, because a
    renamed field and a genuinely shuffle-free job are not the same finding.
    """
    if not stages:
        return {"n_stages": 0, "fields_present": False,
                "shuffle_write_bytes": None, "shuffle_read_bytes": None,
                "top_stages": [],
                "note": "no stages returned -- wrong app id, or the UI was gone"}

    seen_field = False
    rows = []
    for st in stages:
        w = st.get("shuffleWriteBytes")
        r = st.get("shuffleReadBytes")
        if w is not None or r is not None:
            seen_field = True
        rows.append({
            "stage_id": st.get("stageId"),
            "name": (st.get("name") or "")[:80],
            "status": st.get("status"),
            "write_bytes": int(w or 0),
            "read_bytes": int(r or 0),
            "num_tasks": st.get("numTasks"),
            # ── the rest of the stage record (#2654 follow-up) ──────────────
            # `/stages` carries far more than shuffle bytes and this dropped
            # all of it. Every field below answers a question the shuffle
            # totals cannot: whether the gap is CPU or waiting (cpu vs run
            # time), whether it is GC pressure, whether the cluster spilled,
            # and how much of the wall is task time at all.
            #
            # `_int_or_none`, not `int(x or 0)`: a renamed or absent field must
            # stay None so it reads as "not reported", never as a measured
            # zero. That distinction is the whole point of `fields_present`
            # above, and it applies per-field too.
            "executor_run_time_ms": _int_or_none(st.get("executorRunTime")),
            "executor_cpu_time_ns": _int_or_none(st.get("executorCpuTime")),
            "jvm_gc_time_ms": _int_or_none(st.get("jvmGcTime")),
            "memory_spill_bytes": _int_or_none(st.get("memoryBytesSpilled")),
            "disk_spill_bytes": _int_or_none(st.get("diskBytesSpilled")),
            "peak_execution_memory": _int_or_none(st.get("peakExecutionMemory")),
            "input_bytes": _int_or_none(st.get("inputBytes")),
            "output_bytes": _int_or_none(st.get("outputBytes")),
            "input_records": _int_or_none(st.get("inputRecords")),
            "shuffle_write_records": _int_or_none(st.get("shuffleWriteRecords")),
            "shuffle_read_records": _int_or_none(st.get("shuffleReadRecords")),
        })

    if not seen_field:
        return {"n_stages": len(stages), "fields_present": False,
                "shuffle_write_bytes": None, "shuffle_read_bytes": None,
                "top_stages": [],
                "note": ("no stage carried shuffleWriteBytes/shuffleReadBytes -- "
                         "the field names moved; do NOT read this as zero shuffle")}

    rows.sort(key=lambda r: r["write_bytes"] + r["read_bytes"], reverse=True)
    return {
        "n_stages": len(stages),
        "fields_present": True,
        "shuffle_write_bytes": sum(r["write_bytes"] for r in rows),
        "shuffle_read_bytes": sum(r["read_bytes"] for r in rows),
        # Cluster-wide totals. `_sum_or_none` returns None when NO stage
        # reported the field, so a missing metric never sums to a confident 0.
        "shuffle_write_records": _sum_or_none(rows, "shuffle_write_records"),
        "shuffle_read_records": _sum_or_none(rows, "shuffle_read_records"),
        "executor_run_time_ms": _sum_or_none(rows, "executor_run_time_ms"),
        "executor_cpu_time_ns": _sum_or_none(rows, "executor_cpu_time_ns"),
        "jvm_gc_time_ms": _sum_or_none(rows, "jvm_gc_time_ms"),
        "memory_spill_bytes": _sum_or_none(rows, "memory_spill_bytes"),
        "disk_spill_bytes": _sum_or_none(rows, "disk_spill_bytes"),
        "input_bytes": _sum_or_none(rows, "input_bytes"),
        "output_bytes": _sum_or_none(rows, "output_bytes"),
        "num_tasks": _sum_or_none(rows, "num_tasks"),
        "peak_execution_memory_max": _max_or_none(rows, "peak_execution_memory"),
        "top_stages": rows[:top_n],
    }


def _int_or_none(v: Any) -> int | None:
    """`int(v)` when v is a number, else None. NEVER 0 for a missing field."""
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _sum_or_none(rows: list[dict[str, Any]], key: str) -> int | None:
    """Sum a per-stage field, or None when NOT ONE stage reported it.

    The distinction matters: `sum()` over all-None is 0, and a 0 here would be
    read as "no GC time" / "no spill" rather than "Spark did not report it".
    """
    vals = [r[key] for r in rows if r.get(key) is not None]
    return sum(vals) if vals else None


def _max_or_none(rows: list[dict[str, Any]], key: str) -> int | None:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return max(vals) if vals else None


def summarize_executors(executors: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-executor totals from Spark's `/executors` payload.

    Answers what `/stages` cannot: how much heap each executor actually used,
    whether work was spread evenly, and whether any executor died. At 50M the
    likely failure is disk or memory on ONE node, and a cluster-wide total hides
    that -- so the per-executor rows are kept, not just the aggregate.
    """
    if not executors:
        return {"n_executors": 0, "fields_present": False, "executors": [],
                "note": "no executors returned -- wrong app id, or the UI was gone"}

    rows = []
    for ex in executors:
        rows.append({
            "id": ex.get("id"),
            "is_active": ex.get("isActive"),
            "rdd_blocks": _int_or_none(ex.get("rddBlocks")),
            "memory_used": _int_or_none(ex.get("memoryUsed")),
            "disk_used": _int_or_none(ex.get("diskUsed")),
            "max_memory": _int_or_none(ex.get("maxMemory")),
            "total_cores": _int_or_none(ex.get("totalCores")),
            "total_tasks": _int_or_none(ex.get("totalTasks")),
            "failed_tasks": _int_or_none(ex.get("failedTasks")),
            "completed_tasks": _int_or_none(ex.get("completedTasks")),
            "total_duration_ms": _int_or_none(ex.get("totalDuration")),
            "total_gc_time_ms": _int_or_none(ex.get("totalGCTime")),
            "total_shuffle_read": _int_or_none(ex.get("totalShuffleRead")),
            "total_shuffle_write": _int_or_none(ex.get("totalShuffleWrite")),
            "peak_jvm_heap": _peak(ex, "JVMHeapMemory"),
            "peak_jvm_offheap": _peak(ex, "JVMOffHeapMemory"),
            "peak_process_tree_rss": _peak(ex, "ProcessTreeJVMRSSMemory"),
        })

    # A dead executor is the signal that matters most at scale, so surface it
    # as a top-level flag rather than making a reader scan the rows.
    dead = [r["id"] for r in rows if r.get("is_active") is False]
    failed = _sum_or_none(rows, "failed_tasks")
    return {
        "n_executors": len(rows),
        "fields_present": True,
        "dead_executor_ids": dead,
        "any_executor_died": bool(dead),
        "failed_tasks_total": failed,
        "total_gc_time_ms": _sum_or_none(rows, "total_gc_time_ms"),
        "total_duration_ms": _sum_or_none(rows, "total_duration_ms"),
        "disk_used_max": _max_or_none(rows, "disk_used"),
        "peak_jvm_heap_max": _max_or_none(rows, "peak_jvm_heap"),
        "peak_process_tree_rss_max": _max_or_none(rows, "peak_process_tree_rss"),
        "executors": rows,
    }


def _peak(ex: dict[str, Any], key: str) -> int | None:
    """Read one field out of `peakMemoryMetrics`, which may be absent entirely.

    Spark only populates this when `spark.eventLog.logStageExecutorMetrics` (or
    the executor metrics poller) is on. Absent stays None so a reader can tell
    "not collected" from "used no heap".
    """
    pm = ex.get("peakMemoryMetrics")
    if not isinstance(pm, dict):
        return None
    return _int_or_none(pm.get(key))


def fetch(base_url: str, timeout: float = 10.0) -> dict[str, Any]:
    """Shuffle totals for the newest application at `base_url` (a Spark UI).

    Never raises: this is instrumentation attached to a benchmark, and a
    metrics endpoint that is down must not take the measurement with it. The
    error is RECORDED so an absent number reads as "the probe failed", never as
    "there was no shuffle".
    """
    def _get(path: str) -> Any:
        with urllib.request.urlopen(f"{base_url}{path}", timeout=timeout) as fh:
            return json.loads(fh.read().decode("utf-8"))

    try:
        apps = _get("/api/v1/applications")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"error": f"{type(e).__name__}: {str(e)[:160]}", "base_url": base_url}
    if not apps:
        return {"error": "no applications at this UI", "base_url": base_url}

    app_id = apps[0].get("id")
    try:
        stages = _get(f"/api/v1/applications/{app_id}/stages")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"error": f"{type(e).__name__}: {str(e)[:160]}",
                "base_url": base_url, "app_id": app_id}

    out = summarize(stages)
    out["app_id"] = app_id
    out["base_url"] = base_url

    # `/executors` is fetched SEPARATELY and its failure is recorded rather
    # than raised: the stage metrics are the primary measurement and must not
    # be lost because a second endpoint was unavailable. Same contract as the
    # rest of this module -- an absent number says the probe failed.
    try:
        out["executor_metrics"] = summarize_executors(
            _get(f"/api/v1/applications/{app_id}/executors")
        )
    except (urllib.error.URLError, OSError, ValueError) as e:
        out["executor_metrics"] = {
            "error": f"{type(e).__name__}: {str(e)[:160]}", "app_id": app_id,
        }
    return out
