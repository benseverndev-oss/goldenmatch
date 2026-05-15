"""Stage-level timing + candidate-pair accounting for the dedupe pipeline.

Goal: every scale-audit run produces a per-stage breakdown so we know
where the wall actually goes. The 5M baseline (50 min, 11.9 GB peak) is
useless without this — we'd be guessing whether time is in blocking,
scoring, clustering, or autoconfig overhead.

Design constraints:
  * Zero cost when no collector is active (a single context-var lookup +
    `if recorder is None: return`).
  * No required wiring per call site; stages use a context manager and
    can be skipped silently when the recorder isn't pushed.
  * Output is plain dicts (JSON-serializable) so scale-audit reports
    and CI summaries can consume them directly.
  * Concurrency-safe via `ContextVar` (matches the existing
    `profile_emitter` pattern in this package).

Usage:
    from goldenmatch.core.bench import bench_capture, stage, record_metric

    with bench_capture() as bench:
        with stage("ingest"):
            ...
        with stage("blocking"):
            record_metric("block_count", 1234)
            ...
    print(bench.timings)   # {"ingest": 0.42, "blocking": 12.1, ...}
    print(bench.metrics)   # {"block_count": 1234, ...}
"""
from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BenchmarkRecorder:
    """Plain bag-of-numbers populated by pipeline stages.

    Timings accumulate (so reusing the same name sums); metrics
    last-write-wins (so a stage can overwrite an estimate with a final
    number once available).
    """
    timings: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)

    def add_timing(self, name: str, elapsed: float) -> None:
        self.timings[name] = self.timings.get(name, 0.0) + elapsed

    def set_metric(self, key: str, value: Any) -> None:
        self.metrics[key] = value

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_timings_seconds": {k: round(v, 4) for k, v in self.timings.items()},
            "metrics": dict(self.metrics),
        }


_recorder_stack: ContextVar[tuple[BenchmarkRecorder, ...]] = ContextVar(
    "bench_recorder_stack", default=()
)


def current_recorder() -> BenchmarkRecorder | None:
    """Return the active recorder, or None when none is pushed.

    Callers in hot paths should branch on None before doing any work —
    the whole point is to be free when nobody is listening.
    """
    stack = _recorder_stack.get()
    return stack[-1] if stack else None


@contextmanager
def bench_capture() -> Generator[BenchmarkRecorder, None, None]:
    """Push a fresh recorder onto the stack; pop on exit.

    Re-entry within the same context pushes/pops correctly (stages can
    nest a sub-recorder if they need scoped numbers).
    """
    recorder = BenchmarkRecorder()
    prev = _recorder_stack.get()
    token = _recorder_stack.set((*prev, recorder))
    try:
        yield recorder
    finally:
        _recorder_stack.reset(token)


@contextmanager
def stage(name: str) -> Generator[None, None, None]:
    """Time a code block under the active recorder, if any.

    When no recorder is pushed, this is a near-no-op: a single
    ContextVar.get() and an early return. The `time.perf_counter` call
    is still cheap (~50ns) but is skipped when the active recorder is
    None so genuinely zero-cost paths stay zero-cost.
    """
    rec = current_recorder()
    if rec is None:
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        rec.add_timing(name, time.perf_counter() - t0)


def record_metric(key: str, value: Any) -> None:
    """Set a metric on the active recorder, if any.

    Pipeline stages call this with counts/sizes/percentiles that scale
    audits want to publish: ``block_count``, ``scored_pair_count``,
    ``cluster_count``, ``block_size_p99``, etc.
    """
    rec = current_recorder()
    if rec is None:
        return
    rec.set_metric(key, value)


def record_metrics(updates: dict[str, Any]) -> None:
    """Set multiple metrics on the active recorder, if any."""
    rec = current_recorder()
    if rec is None:
        return
    for key, value in updates.items():
        rec.set_metric(key, value)
