"""The Spark metrics reader must never render an absent field as a measured zero.

`scripts/_spark_shuffle_metrics.py` was widened from three fields per stage to
the whole stage record plus `/executors`, so the 50M GoldenMatch-vs-Splink
head-to-head can answer "WHY is one slower" (CPU, GC, spill, skew) rather than
only "how many bytes crossed".

Every added field is a chance to repeat the bug this repo has now hit four
times -- `reduction_ratio == 0.0`, `candidates_compared == 0`,
`mass_above_threshold == 1.0`, and a `grep -c` over a failed command. In each,
a value meaning "nothing measured this" was consumed as evidence.

Here the trap is specific and easy: `sum([])` is `0`. A cluster-wide GC total
that sums an all-missing field returns 0, which reads as "no GC time" rather
than "Spark did not report it" -- and on a benchmark whose entire purpose is
comparing two engines, a fabricated zero on one side is a result-shaped lie.

So these tests assert `is None`, not falsiness. `assert not x` would pass for
both 0 and None and would not catch the regression at all.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MOD = (Path(__file__).resolve().parents[4] / "scripts" / "_spark_shuffle_metrics.py")


def _load():
    if not _MOD.exists():
        pytest.skip(f"{_MOD} not present")
    spec = importlib.util.spec_from_file_location("_spark_shuffle_metrics", _MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _stage(**kw):
    """A stage carrying shuffle bytes (so `fields_present` is True) plus
    whatever else the caller wants to supply."""
    base = {"stageId": 1, "name": "s", "shuffleWriteBytes": 10,
            "shuffleReadBytes": 20, "numTasks": 4}
    base.update(kw)
    return base


# ── the absent-vs-zero contract ────────────────────────────────────────────

def test_a_field_no_stage_reported_is_none_not_zero():
    m = _load()
    out = m.summarize([_stage(), _stage(stageId=2)])
    # Neither stage carried GC / spill / CPU. None of them may read as 0.
    for key in ("jvm_gc_time_ms", "memory_spill_bytes", "disk_spill_bytes",
                "executor_cpu_time_ns", "executor_run_time_ms"):
        assert out[key] is None, (
            f"{key} summed to {out[key]!r} when NO stage reported it; "
            "sum([]) == 0 and a 0 here reads as a measurement"
        )


def test_a_field_some_stages_reported_sums_only_those():
    m = _load()
    out = m.summarize([
        _stage(jvmGcTime=100),
        _stage(stageId=2),            # no jvmGcTime at all
        _stage(stageId=3, jvmGcTime=50),
    ])
    assert out["jvm_gc_time_ms"] == 150


def test_a_genuine_zero_is_preserved_as_zero():
    """The other half of the contract: a REPORTED 0 must stay 0, not become
    None. Otherwise 'no spill happened' becomes unsayable."""
    m = _load()
    out = m.summarize([_stage(diskBytesSpilled=0), _stage(stageId=2, diskBytesSpilled=0)])
    assert out["disk_spill_bytes"] == 0


def test_shuffle_bytes_still_behave_as_before():
    """The original contract must not have moved."""
    m = _load()
    out = m.summarize([_stage(), _stage(stageId=2)])
    assert out["fields_present"] is True
    assert out["shuffle_write_bytes"] == 20
    assert out["shuffle_read_bytes"] == 40
    assert out["n_stages"] == 2


def test_no_stages_is_still_distinguished_from_no_shuffle():
    m = _load()
    empty = m.summarize([])
    assert empty["fields_present"] is False
    assert empty["shuffle_write_bytes"] is None

    no_shuffle = m.summarize([{"stageId": 1, "name": "s", "numTasks": 1}])
    assert no_shuffle["fields_present"] is False
    assert no_shuffle["shuffle_write_bytes"] is None
    assert "do NOT read this as zero shuffle" in no_shuffle["note"]


def test_a_non_numeric_value_is_none_rather_than_a_crash():
    """Instrumentation must never take the benchmark down with it."""
    m = _load()
    out = m.summarize([_stage(jvmGcTime="n/a")])
    assert out["jvm_gc_time_ms"] is None


# ── /executors ─────────────────────────────────────────────────────────────

def _exec(**kw):
    base = {"id": "0", "isActive": True, "totalTasks": 10, "failedTasks": 0}
    base.update(kw)
    return base


def test_a_dead_executor_is_surfaced_at_the_top_level():
    """At 50M the likely failure is disk or memory on ONE node, and a
    cluster-wide total hides it."""
    m = _load()
    out = m.summarize_executors([
        _exec(id="0"),
        _exec(id="1", isActive=False),
    ])
    assert out["any_executor_died"] is True
    assert out["dead_executor_ids"] == ["1"]


def test_a_healthy_cluster_reports_no_deaths():
    m = _load()
    out = m.summarize_executors([_exec(id="0"), _exec(id="1")])
    assert out["any_executor_died"] is False
    assert out["dead_executor_ids"] == []


def test_peak_memory_metrics_absent_is_none():
    """`peakMemoryMetrics` is only populated when the executor metrics poller
    is on. Absent must not read as 'used no heap'."""
    m = _load()
    out = m.summarize_executors([_exec()])
    assert out["peak_jvm_heap_max"] is None
    assert out["executors"][0]["peak_jvm_heap"] is None


def test_peak_memory_metrics_present_is_read():
    m = _load()
    out = m.summarize_executors([
        _exec(id="0", peakMemoryMetrics={"JVMHeapMemory": 111,
                                         "ProcessTreeJVMRSSMemory": 222}),
        _exec(id="1", peakMemoryMetrics={"JVMHeapMemory": 999,
                                         "ProcessTreeJVMRSSMemory": 333}),
    ])
    assert out["peak_jvm_heap_max"] == 999
    assert out["peak_process_tree_rss_max"] == 333


def test_no_executors_is_distinguished_from_no_data():
    m = _load()
    out = m.summarize_executors([])
    assert out["fields_present"] is False
    assert out["n_executors"] == 0
