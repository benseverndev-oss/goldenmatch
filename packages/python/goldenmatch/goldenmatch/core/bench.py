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
  * **Thread-safe writes via a lock.** Worker threads in
    ``score_blocks_parallel`` may call ``add_timing`` / ``set_metric``
    concurrently. Without a lock, parallel dict writes don't crash
    but lost updates corrupt the totals. With a lock, contention is
    bounded — each write is ~50ns and stage entries are infrequent
    compared to the actual work the stage measures.

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

Avoiding the hot-path tax
-------------------------

Do NOT wrap ``with stage(...)`` inside per-block / per-pair hot loops
running under ``ThreadPoolExecutor`` — even with the lock, the GIL
acquire/release dance contends with ``rapidfuzz.cdist``'s GIL release
and can slow the whole pipeline by ~5x (verified on the 100K audit:
24s without per-scorer stages, 127s with them). Reserve stage wrappers
for the single-threaded pipeline driver (``_run_dedupe_pipeline``) and
for stage-grained calls in worker threads (e.g. one stage per block,
not per scorer-per-block).
"""
from __future__ import annotations

import sys
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

# RSS sampling: `resource.getrusage().ru_maxrss` is the process-lifetime peak
# resident set size (Linux: KB, macOS: bytes). It's monotonically non-decreasing
# — sampling at every stage exit gives the cumulative curve, and diffing
# consecutive stages gives each stage's contribution to the peak. Windows has
# no equivalent in the stdlib, so the field stays empty there (rare on bench
# paths — every bench-* workflow runs on Linux).
try:
    import resource  # type: ignore[import-not-found]
    _HAS_RESOURCE = True
except ImportError:  # pragma: no cover - Windows path, exercised manually
    resource = None  # type: ignore[assignment]
    _HAS_RESOURCE = False


def _peak_rss_kb() -> int | None:
    """Process-lifetime peak RSS in KB, or None on Windows."""
    if not _HAS_RESOURCE:
        return None
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # type: ignore[union-attr]
    # Linux reports KB; macOS reports bytes. Normalize to KB.
    return int(ru) if sys.platform != "darwin" else int(ru) // 1024


@dataclass
class BenchmarkRecorder:
    """Plain bag-of-numbers populated by pipeline stages.

    Timings accumulate (so reusing the same name sums); metrics
    last-write-wins (so a stage can overwrite an estimate with a final
    number once available).

    Thread-safety: ``add_timing`` and ``set_metric`` are protected by
    ``_lock`` so worker threads in ``score_blocks_parallel`` don't
    corrupt totals via lost updates. Reads are not locked — the dict
    snapshots taken in ``to_dict`` may catch a partial write but the
    inaccuracy is bounded by the duration of one update (~50ns).
    """
    timings: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    # Peak RSS (KB) observed at each stage's exit. ru_maxrss is monotonic, so
    # the LATEST exit-time value per stage name wins (last-write-wins matches
    # the existing metrics shape; reusing a stage name overwrites). To get
    # per-stage contribution to the peak, diff consecutive entries in
    # insertion order. Empty on Windows (resource module unavailable).
    stage_peak_rss_kb: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def add_timing(self, name: str, elapsed: float) -> None:
        with self._lock:
            self.timings[name] = self.timings.get(name, 0.0) + elapsed

    def set_metric(self, key: str, value: Any) -> None:
        with self._lock:
            self.metrics[key] = value

    def set_stage_peak_rss(self, name: str, peak_kb: int) -> None:
        """Record the process-lifetime peak RSS observed at stage exit.

        Called from ``stage(...)`` after the timed block completes. No-op on
        Windows (caller passes the result of ``_peak_rss_kb`` which is None
        there, but we accept int only — the caller branches).
        """
        with self._lock:
            self.stage_peak_rss_kb[name] = peak_kb

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_timings_seconds": {k: round(v, 4) for k, v in self.timings.items()},
            "stage_peak_rss_kb": dict(self.stage_peak_rss_kb),
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
        # Record the process-lifetime peak RSS at stage exit. ru_maxrss is a
        # cheap syscall (microseconds) and only runs once per stage, so this
        # doesn't violate the hot-path-tax rule documented above. Skip on
        # Windows where the resource module is unavailable.
        peak = _peak_rss_kb()
        if peak is not None:
            rec.set_stage_peak_rss(name, peak)


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
