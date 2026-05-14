"""Scale audit harness: measure goldenmatch's memory + wall-clock at scale.

Background (Issue #171, PR #120 strategy doc): the "1M records: OOM in-memory"
note in CLAUDE.md is ~6 months old and predates the v1.7-v1.12 controller
work. Before changing any code path or wiring an autoselect threshold, we
need a current profile of where memory actually goes at each row count and
which pipeline stage dominates.

This harness:
  1. Generates a synthetic person fixture at the requested row count
     (reusing `tests/generate_synthetic.py`).
  2. Runs the dedupe pipeline in-memory under tracemalloc + psutil RSS
     sampling.
  3. Times each named stage of `run_dedupe_df`.
  4. Emits a structured JSON result and a one-line summary.

Usage:
  python scripts/scale_audit.py --rows 100000 --out scale_100k.json
  python scripts/scale_audit.py --rows 500000 --out scale_500k.json

Aggregate runs into `docs/scale-audit-2026-05.md` by calling
`scale_audit.py --summarize scale_*.json` (see __main__).

Why a separate script and not a pytest case: scale runs are too slow
(minutes per row count) for CI's per-test timeout, and tracemalloc's
overhead can perturb pytest-collected timing. This is a benchmark
harness, not a regression test. CI gating arrives in step 4 of #171.
"""
from __future__ import annotations

import argparse
import faulthandler
import gc
import json
import os
import sys
import threading
import time
import tracemalloc
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Enable faulthandler at import time. Catches segfaults / SIGABRT /
# stack overflows from C extensions that don't propagate as Python
# exceptions. The 1M auto_configure SystemError might be the polite
# variant of a C-level crash; faulthandler will surface the C stack
# (modulo symbol availability) when the polite path doesn't.
faulthandler.enable()

try:
    import psutil
except ImportError:
    sys.stderr.write(
        "psutil required for scale_audit. Install via `uv pip install psutil`.\n"
    )
    sys.exit(2)


# ── Stage timer ─────────────────────────────────────────────────────────


@dataclass
class StageMeasurement:
    """One pipeline stage's wall + memory snapshot."""
    name: str
    wall_seconds: float
    rss_peak_mb: float          # max RSS observed during the stage
    rss_delta_mb: float         # RSS at stage end minus RSS at stage start
    tracemalloc_peak_mb: float  # peak Python heap allocations during stage


@dataclass
class ScaleAuditResult:
    """One row-count's full measurement."""
    rows: int
    duplicate_rate: float
    fixture_path: str
    fixture_size_mb: float
    total_wall_seconds: float
    peak_rss_mb: float
    peak_tracemalloc_mb: float
    stages: list[StageMeasurement] = field(default_factory=list)
    cluster_count: int = 0
    notes: str = ""
    failed: str | None = None    # exception type:message if the run died


class _StageTimer:
    """Context manager that records wall + RSS + tracemalloc for one stage.

    Sampled RSS only at start/end; for finer-grained sampling we'd need a
    background thread. The current `psutil.Process().memory_info().rss`
    snapshot is good enough to identify the dominant stage — the actual
    allocation source identification comes from tracemalloc.
    """

    def __init__(self, name: str, result: ScaleAuditResult, process: psutil.Process):
        self.name = name
        self.result = result
        self.process = process
        self._t0: float = 0.0
        self._rss0: int = 0
        self._tm_peak_before: int = 0

    def __enter__(self) -> _StageTimer:
        # Force a GC before each stage so RSS deltas attribute to the stage
        # rather than to deferred cleanup from a prior stage.
        gc.collect()
        self._t0 = time.perf_counter()
        self._rss0 = self.process.memory_info().rss
        # tracemalloc peak is monotonic-since-start; capture pre-stage value
        # so we can compute per-stage peak as (post - pre) where meaningful.
        _, peak_before = tracemalloc.get_traced_memory()
        self._tm_peak_before = peak_before
        # Reset the peak so the next get_traced_memory() reads stage-local.
        tracemalloc.reset_peak()
        return self

    def __exit__(self, *_exc: Any) -> None:
        wall = time.perf_counter() - self._t0
        rss_now = self.process.memory_info().rss
        _, tm_peak = tracemalloc.get_traced_memory()
        self.result.stages.append(StageMeasurement(
            name=self.name,
            wall_seconds=wall,
            rss_peak_mb=rss_now / 1024 / 1024,  # snapshot-end RSS; rough
            rss_delta_mb=(rss_now - self._rss0) / 1024 / 1024,
            tracemalloc_peak_mb=tm_peak / 1024 / 1024,
        ))


# ── Fixture ─────────────────────────────────────────────────────────────


def ensure_fixture(rows: int, dupe_rate: float, fixture_dir: Path) -> Path:
    """Generate a synthetic fixture if it doesn't already exist.

    Fixtures live under `.profile_tmp/scale_fixtures/` (gitignored) so the
    audit is reproducible without bloating the repo. The filename encodes
    `rows` + `dupe_rate` so different dupe-rate sweeps don't collide.
    """
    fixture_dir.mkdir(parents=True, exist_ok=True)
    path = fixture_dir / f"synthetic_{rows}_dupe{int(dupe_rate*100):02d}.csv"
    if path.exists():
        return path

    # The synthetic generator lives in tests/ since it's also used by
    # autoconfig regression tests. Import from there to avoid duplication.
    sys.path.insert(0, str(Path(__file__).parent.parent / "packages" / "python" / "goldenmatch"))
    from tests.generate_synthetic import generate  # type: ignore[import-not-found]

    # ASCII arrow (not ->) — Windows cp1252 terminals can't encode →.
    # CLAUDE.md flags this as a recurring trap in benchmark scripts.
    print(f"[fixture] generating {rows:,} rows (dupe_rate={dupe_rate}) -> {path.name}")
    generate(path, n_records=rows, dupe_rate=dupe_rate)
    return path


# ── The audit run ───────────────────────────────────────────────────────


@contextmanager
def _tracking(
    result: ScaleAuditResult,
    process: psutil.Process,
    enable_tracemalloc: bool = True,
    cprofile_path: Path | None = None,
):
    """Wrap the whole run with optional tracemalloc + cProfile + a wall timer.

    tracemalloc retains a traceback per allocation, which on large runs
    becomes a meaningful chunk of process memory itself — defeating the
    "what's the real peak?" question. Pass ``enable_tracemalloc=False``
    for cloud runs where we care about true RSS without the tracker's
    overhead. Stage-level peaks are still captured via psutil RSS sampling
    regardless.

    ``cprofile_path`` (optional): when set, wraps the audit in
    ``cProfile.Profile`` and dumps stats to that path on exit. cProfile's
    overhead is ~30% wall — fine for "where does time go?" attribution
    runs, but turn it off for memory-only / clean-wall measurements.
    """
    import cProfile  # stdlib; lazy import keeps the no-profile path light.

    if enable_tracemalloc:
        tracemalloc.start()
    profiler: cProfile.Profile | None = None
    if cprofile_path is not None:
        profiler = cProfile.Profile()
        profiler.enable()
    t0 = time.perf_counter()
    rss0 = process.memory_info().rss
    try:
        yield
    finally:
        result.total_wall_seconds = time.perf_counter() - t0
        if profiler is not None and cprofile_path is not None:
            profiler.disable()
            cprofile_path.parent.mkdir(parents=True, exist_ok=True)
            profiler.dump_stats(str(cprofile_path))
        if enable_tracemalloc:
            _, tm_peak = tracemalloc.get_traced_memory()
            result.peak_tracemalloc_mb = tm_peak / 1024 / 1024
            tracemalloc.stop()
        # Final RSS is approximate peak — RSS doesn't shrink immediately on
        # Linux; close enough for a "did this fit on a 64GB box" question.
        rss_now = process.memory_info().rss
        result.peak_rss_mb = max(rss_now, rss0) / 1024 / 1024


def _write_snapshot(result: ScaleAuditResult, out_path: Path | None) -> None:
    """Flush `result` to disk. No-op when no out_path was supplied.

    Called after every stage so an OS-level OOM-kill (which never reaches
    our `except`) still leaves us with the completed stages' measurements.
    Without this, a kill mid-`run_dedupe` would lose `auto_configure`'s
    50-minute result too — total loss instead of partial evidence.
    """
    if out_path is None:
        return
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file then atomic-replace so a kill mid-write
        # doesn't leave a corrupt JSON on disk.
        tmp = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp.write_text(json.dumps(asdict(result), indent=2))
        os.replace(tmp, out_path)
    except Exception as exc:
        # Snapshot is best-effort; never let it crash the run.
        sys.stderr.write(f"[scale-audit] snapshot flush failed: {exc}\n")


class _RSSWatchdog:
    """Background thread that aborts the run when RSS exceeds a budget.

    The OS will kill the process before Python sees `MemoryError` on
    most allocations that matter (large Polars buffers, Arrow chunks,
    pyo3 round-trips). This watchdog gives us a chance to flush the
    partial result and exit cleanly before the OS gets to us.

    Trip behaviour: write a final snapshot with `note` populated, then
    `os._exit(1)`. Regular `sys.exit` won't unwind if the main thread
    is in a long-running C call (rapidfuzz, Polars), so we hard-exit.
    """

    def __init__(
        self,
        budget_mb: float,
        process: psutil.Process,
        result: ScaleAuditResult,
        out_path: Path | None,
        flush_fn: Callable[[ScaleAuditResult, Path | None], None],
        sample_interval_s: float = 0.5,
    ):
        # Trip at 90% of budget so we flush before the OS kills us.
        self.trip_bytes = int(budget_mb * 0.9 * 1024 * 1024)
        self.budget_mb = budget_mb
        self.process = process
        self.result = result
        self.out_path = out_path
        self.flush_fn = flush_fn
        self.sample_interval_s = sample_interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._peak_rss_bytes = 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="rss-watchdog")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    @property
    def peak_rss_mb(self) -> float:
        return self._peak_rss_bytes / 1024 / 1024

    def _loop(self) -> None:
        while not self._stop.wait(self.sample_interval_s):
            try:
                rss = self.process.memory_info().rss
            except Exception:
                continue
            if rss > self._peak_rss_bytes:
                self._peak_rss_bytes = rss
            if rss > self.trip_bytes:
                # Trip. Record the abort reason on the result, flush, hard-exit.
                self.result.failed = (
                    f"RSSBudgetExceeded: {rss/1024/1024:.0f}MB > "
                    f"budget {self.budget_mb:.0f}MB (trip at "
                    f"{self.trip_bytes/1024/1024:.0f}MB)"
                )
                self.result.notes = (self.result.notes or "") + (
                    "; aborted by RSS watchdog before OS OOM-kill"
                )
                # Capture whatever timing we have. tracemalloc may still be
                # active — guard against that.
                try:
                    _, tm_peak = tracemalloc.get_traced_memory()
                    self.result.peak_tracemalloc_mb = max(
                        self.result.peak_tracemalloc_mb, tm_peak / 1024 / 1024
                    )
                except Exception:
                    pass
                self.result.peak_rss_mb = max(self.result.peak_rss_mb, rss / 1024 / 1024)
                try:
                    self.flush_fn(self.result, self.out_path)
                finally:
                    sys.stderr.write(
                        f"[scale-audit] aborted: RSS exceeded {self.budget_mb:.0f}MB budget\n"
                    )
                    os._exit(1)


# ── Backend execution helpers ───────────────────────────────────────────


def _run_polars_direct(
    result: ScaleAuditResult,
    process: psutil.Process,
    out_path: Path | None,
    fixture_path: Path,
) -> None:
    """In-Python zero-config dedupe path. Same as `dedupe_df(df)` exercises."""
    import polars as pl
    from goldenmatch.core.autoconfig import auto_configure_df
    from goldenmatch.core.pipeline import run_dedupe_df

    with _StageTimer("read_csv", result, process):
        df = pl.read_csv(fixture_path, encoding="utf8-lossy", ignore_errors=True)
    _write_snapshot(result, out_path)

    with _StageTimer("auto_configure", result, process):
        # _skip_finalize=True matches the production dedupe_df() path
        # (see goldenmatch/_api.py). Without this flag the audit double-
        # counts a full pipeline run in the auto_configure stage.
        config = auto_configure_df(df, _skip_finalize=True)
    _write_snapshot(result, out_path)

    with _StageTimer("run_dedupe", result, process):
        pipeline_result = run_dedupe_df(df, config, output_clusters=True, auto_config=False)
    _write_snapshot(result, out_path)

    clusters = pipeline_result.get("clusters") or {}
    result.cluster_count = len(clusters)


def _build_explicit_personlike_config():
    """Hand-tuned config for the synthetic person fixture.

    Used by ``config_mode="explicit-personlike"`` to bypass the controller
    when its small-sample blocking-discovery heuristic fails (as it does
    at 5M when 1K-sample dupes are too thin for matchkey learning).
    Pins blocking on ``last_name + zip`` and a weighted matchkey across
    ``first_name + last_name + address``. Suitable for any name-shaped
    fixture; not portable to bibliographic / product / address shapes.
    """
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
        QualityConfig,
    )
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="person_fuzzy",
                type="weighted",
                threshold=0.85,
                fields=[
                    MatchkeyField(field="first_name", scorer="jaro_winkler", weight=0.3,
                                  transforms=["lowercase", "strip"]),
                    MatchkeyField(field="last_name", scorer="jaro_winkler", weight=0.4,
                                  transforms=["lowercase", "strip"]),
                    MatchkeyField(field="address", scorer="token_sort", weight=0.3,
                                  transforms=["lowercase", "strip"]),
                ],
            ),
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["last_name", "zip"],
                                    transforms=["lowercase", "strip"])],
            max_block_size=5000,
        ),
        quality=QualityConfig(mode="disabled", enabled=False),
    )


def _run_duckdb_backend(
    result: ScaleAuditResult,
    process: psutil.Process,
    out_path: Path | None,
    fixture_path: Path,
    config_mode: str = "auto",
) -> None:
    """In-Python dedupe with ``config.backend = "duckdb"``.

    Same Python entry point as ``polars-direct`` (Polars frame at the
    boundary, dedupe_df-shaped call), but routes block scoring through
    the in-package DuckDB backend (``goldenmatch.backends.duckdb_backend``).
    Targets the "5M-on-32GB without OOM" question: Polars holds the
    frame, DuckDB owns the score-pair tables out-of-core.

    Distinct from ``duckdb-udf``, which is the external goldenmatch-duckdb
    extension surface (UDFs registered on a DuckDB connection, called via
    SQL). This lane is the in-process Python backend.
    """
    import polars as pl
    from goldenmatch.config.schemas import QualityConfig
    from goldenmatch.core.autoconfig import auto_configure_df
    from goldenmatch.core.pipeline import run_dedupe_df

    with _StageTimer("read_csv", result, process):
        df = pl.read_csv(fixture_path, encoding="utf8-lossy", ignore_errors=True)
    _write_snapshot(result, out_path)

    with _StageTimer("auto_configure", result, process):
        if config_mode == "explicit-personlike":
            config = _build_explicit_personlike_config()
            config.backend = "duckdb"
        else:
            config = auto_configure_df(df, _skip_finalize=True)
            config.backend = "duckdb"
            # GoldenCheck quality scan is not what we're measuring; disabling
            # also avoids the messy-input → temp-CSV → re-read path that fails
            # on whitespace-wrapped numerics in the synthetic fixture.
            if config.quality is None:
                config.quality = QualityConfig(mode="disabled", enabled=False)
            else:
                config.quality.mode = "disabled"
                config.quality.enabled = False
    _write_snapshot(result, out_path)

    with _StageTimer("run_dedupe", result, process):
        pipeline_result = run_dedupe_df(df, config, output_clusters=True, auto_config=False)
    _write_snapshot(result, out_path)

    clusters = pipeline_result.get("clusters") or {}
    result.cluster_count = len(clusters)


def _run_chunked(
    result: ScaleAuditResult,
    process: psutil.Process,
    out_path: Path | None,
    fixture_path: Path,
    config_mode: str = "auto",
    chunk_size: int = 100_000,
) -> None:
    """Streaming chunked dedupe via ``ChunkedMatcher.process_file``.

    Reads the fixture in fixed-size row chunks via ``scan_csv().slice()``
    instead of materializing the full frame in memory. Targets the
    "fits in N GB regardless of row count" claim — peak RSS should
    be a function of ``chunk_size``, not file size.

    Doesn't use auto_configure_df because the controller's small-sample
    blocking discovery has been observed to fail at 5M (see PR audit).
    ``config_mode="explicit-personlike"`` pins a hand-tuned config;
    ``config_mode="auto"`` still calls auto-config but on the full
    in-memory frame, which defeats the streaming claim — only use it
    for small fixtures.
    """
    from goldenmatch.core.chunked import ChunkedMatcher

    if config_mode == "explicit-personlike":
        config = _build_explicit_personlike_config()
    else:
        # Match polars-direct's auto-config path so a smoke run at small
        # sizes still works zero-config. At 5M+ pass explicit-personlike.
        import polars as pl
        from goldenmatch.core.autoconfig import auto_configure_df
        df = pl.read_csv(fixture_path, encoding="utf8-lossy", ignore_errors=True)
        config = auto_configure_df(df, _skip_finalize=True)

    with _StageTimer("chunked_process", result, process):
        matcher = ChunkedMatcher(config=config, chunk_size=chunk_size)
        stats = matcher.process_file(fixture_path)
    _write_snapshot(result, out_path)

    result.cluster_count = int(stats.get("total_clusters", 0))


def _run_duckdb_udf(
    result: ScaleAuditResult,
    process: psutil.Process,
    out_path: Path | None,
    fixture_path: Path,
) -> None:
    """Exercise the goldenmatch-duckdb UDF surface.

    Loads the fixture into a DuckDB table, registers the goldenmatch_*
    UDFs, calls `goldenmatch_dedupe_table('fixture', '{}')` via SQL.
    Measures the storage-and-call overhead a SQL-first user would pay,
    on top of the in-Python dedupe cost (the UDF reads the table back
    via `con.cursor().sql(...).pl()` and calls `dedupe_df` in-process).
    """
    import duckdb
    from goldenmatch_duckdb.functions import register as register_udfs

    with _StageTimer("duckdb_load", result, process):
        con = duckdb.connect()
        con.sql(
            f"CREATE TABLE fixture AS SELECT * FROM read_csv_auto('{fixture_path.as_posix()}')",
        )
    _write_snapshot(result, out_path)

    with _StageTimer("register_udfs", result, process):
        register_udfs(con)
    _write_snapshot(result, out_path)

    with _StageTimer("dedupe_via_udf", result, process):
        # Empty config triggers the same zero-config controller path that
        # _run_polars_direct's auto_configure_df + run_dedupe_df pair
        # exercises. The UDF returns the golden records as JSON; we count
        # via duckdb's own row counting on a follow-up to avoid Python
        # round-trip of the JSON blob.
        out = con.sql("SELECT goldenmatch_dedupe_table('fixture', '{}')").fetchone()
        # The UDF response is a JSON string of golden records. Counting via
        # json.loads is cheap relative to the dedupe work itself.
        import json as _json
        golden_records_json = out[0] if out else None
        if golden_records_json:
            try:
                # Polars `write_json()` produces a JSON array of objects.
                records = _json.loads(golden_records_json)
                result.cluster_count = len(records) if isinstance(records, list) else 0
            except Exception:
                # Some configs return stats instead of records; cluster_count
                # stays 0 in that branch but the rest of the measurement is
                # still informative.
                result.cluster_count = 0
    _write_snapshot(result, out_path)


def run_audit(
    rows: int,
    dupe_rate: float = 0.15,
    fixture_dir: Path | None = None,
    out_path: Path | None = None,
    rss_budget_mb: float | None = None,
    enable_tracemalloc: bool = True,
    cprofile_path: Path | None = None,
    backend: str = "polars-direct",
    config_mode: str = "auto",
) -> ScaleAuditResult:
    """Run one audit pass against a synthetic fixture of the given size.

    `out_path` (optional): if supplied, a JSON snapshot is written after
    every stage. An OS-level OOM-kill mid-run still leaves us with the
    completed stages.

    `rss_budget_mb` (optional): start an RSS watchdog that aborts and
    flushes when the process's RSS exceeds `budget * 0.9`. None disables.

    `backend`: which goldenmatch entry surface to time.
      - "polars-direct" (default): the Python `dedupe_df(df)` zero-config
        path, identical to what `goldenmatch dedupe customers.csv` runs.
      - "duckdb-udf": exercise the `goldenmatch-duckdb` package — load
        the fixture into a DuckDB table, register the UDFs, and call
        `goldenmatch_dedupe_table('fixture', '{}')` via SQL. Measures
        the storage-and-call overhead that SQL-first users would pay.
    """
    fixture_dir = fixture_dir or (Path(__file__).parent.parent / ".profile_tmp" / "scale_fixtures")
    fixture_path = ensure_fixture(rows, dupe_rate, fixture_dir)

    result = ScaleAuditResult(
        rows=rows,
        duplicate_rate=dupe_rate,
        fixture_path=str(fixture_path),
        fixture_size_mb=fixture_path.stat().st_size / 1024 / 1024,
        total_wall_seconds=0.0,
        peak_rss_mb=0.0,
        peak_tracemalloc_mb=0.0,
    )

    process = psutil.Process()

    # Flush a snapshot before the run starts too — gives consumers a file
    # to wait on, and proves the out_path is writable before the long run.
    _write_snapshot(result, out_path)

    watchdog: _RSSWatchdog | None = None
    if rss_budget_mb is not None:
        watchdog = _RSSWatchdog(
            budget_mb=rss_budget_mb,
            process=process,
            result=result,
            out_path=out_path,
            flush_fn=_write_snapshot,
        )
        watchdog.start()

    try:
        with _tracking(
            result, process,
            enable_tracemalloc=enable_tracemalloc,
            cprofile_path=cprofile_path,
        ):
            if backend == "polars-direct":
                _run_polars_direct(result, process, out_path, fixture_path)
            elif backend == "duckdb-backend":
                _run_duckdb_backend(result, process, out_path, fixture_path, config_mode=config_mode)
            elif backend == "chunked":
                _run_chunked(result, process, out_path, fixture_path, config_mode=config_mode)
            elif backend == "duckdb-udf":
                _run_duckdb_udf(result, process, out_path, fixture_path)
            else:
                raise ValueError(f"unknown backend: {backend!r}")
    except BaseException as exc:
        # Capturing the failure rather than re-raising — partial measurements
        # from earlier stages are still informative (often the OOM hits in a
        # specific stage and the others' timings are still valid). Use
        # BaseException (not Exception) because some C-extension faults
        # propagate as SystemError or SystemExit on the cpython side.
        result.failed = f"{type(exc).__name__}: {exc}"
        # Also surface the full traceback on stderr — getting "SystemError:
        # error return without exception set" with no stack frame is useless
        # for debugging. Doesn't change the JSON shape; just helps the human
        # reading the log file.
        import traceback as _tb
        sys.stderr.write("[scale-audit] caught during audit run:\n")
        sys.stderr.write(_tb.format_exc())
    finally:
        if watchdog is not None:
            # If the watchdog observed a higher RSS than _tracking's
            # start/end snapshots, promote that — the watchdog samples
            # every 0.5s and catches transient peaks the endpoint check
            # misses.
            result.peak_rss_mb = max(result.peak_rss_mb, watchdog.peak_rss_mb)
            watchdog.stop()
        _write_snapshot(result, out_path)

    return result


# ── Report rendering ────────────────────────────────────────────────────


def render_summary(results: list[ScaleAuditResult]) -> str:
    """Render a single Markdown table summarising all measured row counts."""
    if not results:
        return "_no results_\n"
    lines = [
        "| rows | wall (s) | peak RSS (MB) | peak Python heap (MB) | clusters | status |",
        "|---:|---:|---:|---:|---:|:---|",
    ]
    for r in results:
        status = r.failed if r.failed else "ok"
        lines.append(
            f"| {r.rows:,} | {r.total_wall_seconds:.2f} | {r.peak_rss_mb:.1f} "
            f"| {r.peak_tracemalloc_mb:.1f} | {r.cluster_count:,} | {status} |"
        )
    return "\n".join(lines) + "\n"


def render_per_stage(result: ScaleAuditResult) -> str:
    """One row-count's per-stage breakdown."""
    lines = [
        f"### {result.rows:,} rows (dupe_rate={result.duplicate_rate})",
        "",
        "| stage | wall (s) | RSS Δ (MB) | tracemalloc peak (MB) |",
        "|---|---:|---:|---:|",
    ]
    for s in result.stages:
        lines.append(
            f"| {s.name} | {s.wall_seconds:.2f} | {s.rss_delta_mb:+.1f} "
            f"| {s.tracemalloc_peak_mb:.1f} |"
        )
    if result.failed:
        lines.append("")
        lines.append(f"**Failed during this run:** `{result.failed}`")
    return "\n".join(lines) + "\n"


# ── CLI ─────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--rows", type=int, help="row count for a single audit pass")
    ap.add_argument("--dupe-rate", type=float, default=0.15)
    ap.add_argument(
        "--out",
        type=Path,
        help=(
            "write JSON result to this path. With per-stage flushing enabled, "
            "a partial snapshot is written after every stage — if the process "
            "is OOM-killed, completed stages' data is preserved on disk."
        ),
    )
    ap.add_argument(
        "--rss-budget-mb",
        type=float,
        help=(
            "abort the run when peak RSS exceeds this budget. The watchdog "
            "trips at 90%% of the budget so it gets to flush before the OS "
            "OOM-kills the process. Recommended for runs above 500K rows."
        ),
    )
    ap.add_argument(
        "--no-tracemalloc",
        action="store_true",
        help=(
            "disable tracemalloc tracking. tracemalloc retains a traceback per "
            "allocation, which on large runs adds non-trivial memory overhead. "
            "Recommended for cloud-runner peak-memory measurements where psutil "
            "RSS is the metric of interest."
        ),
    )
    ap.add_argument(
        "--cprofile",
        type=Path,
        help=(
            "wrap the audit in cProfile and dump stats to this path. "
            "Adds ~30%% wall overhead — use for `where does time go?` "
            "attribution runs, not for clean-wall measurements. The .prof "
            "file can be loaded with `python -m pstats <path>` or visualised "
            "via snakeviz / gprof2dot."
        ),
    )
    ap.add_argument(
        "--config-mode",
        choices=("auto", "explicit-personlike"),
        default="auto",
        help=(
            "config-resolution strategy. auto (default) calls "
            "auto_configure_df, which is the production zero-config path. "
            "explicit-personlike pins a hand-tuned config for the synthetic "
            "person fixture (last_name+zip blocking, weighted matchkey on "
            "first/last/address). Use this to isolate backend perf from "
            "controller behavior at scale — at 5M+ the controller's "
            "1K-sample blocking discovery can fail with sparse duplicates "
            "and fall back to near-quadratic scoring. Only affects "
            "--backend duckdb-backend currently."
        ),
    )
    ap.add_argument(
        "--backend",
        choices=("polars-direct", "duckdb-backend", "chunked", "duckdb-udf"),
        default="polars-direct",
        help=(
            "which goldenmatch surface to time. polars-direct (default) "
            "exercises the Python `dedupe_df(df)` zero-config path. "
            "duckdb-backend uses the same Python entry point but sets "
            "config.backend='duckdb' (currently a no-op for processing — "
            "documented in the 5M audit). chunked uses ChunkedMatcher to "
            "stream the fixture via scan_csv().slice() in fixed-size row "
            "chunks — the only path that actually fits 5M in commodity "
            "memory. duckdb-udf loads the fixture into a DuckDB table, "
            "registers the goldenmatch-duckdb extension UDFs, and calls "
            "goldenmatch_dedupe_table via SQL."
        ),
    )
    ap.add_argument(
        "--summarize",
        nargs="+",
        help="aggregate previously-written JSON results into a Markdown summary on stdout",
    )
    args = ap.parse_args()

    if args.summarize:
        results = []
        for p in args.summarize:
            with open(p) as f:
                d = json.load(f)
            # Reconstruct (no need for full dataclass parity — just enough for
            # the renderer to read the fields it uses).
            r = ScaleAuditResult(
                rows=d["rows"],
                duplicate_rate=d["duplicate_rate"],
                fixture_path=d["fixture_path"],
                fixture_size_mb=d["fixture_size_mb"],
                total_wall_seconds=d["total_wall_seconds"],
                peak_rss_mb=d["peak_rss_mb"],
                peak_tracemalloc_mb=d["peak_tracemalloc_mb"],
                stages=[StageMeasurement(**s) for s in d.get("stages", [])],
                cluster_count=d.get("cluster_count", 0),
                failed=d.get("failed"),
            )
            results.append(r)
        results.sort(key=lambda r: r.rows)
        print(render_summary(results))
        for r in results:
            print(render_per_stage(r))
        return

    if not args.rows:
        ap.error("--rows or --summarize required")

    # run_audit handles per-stage flushing when out_path is provided.
    # No extra write here unless --out was omitted, in which case we
    # still emit the final blob to stdout for piping.
    result = run_audit(
        rows=args.rows,
        dupe_rate=args.dupe_rate,
        out_path=args.out,
        rss_budget_mb=args.rss_budget_mb,
        enable_tracemalloc=not args.no_tracemalloc,
        cprofile_path=args.cprofile,
        backend=args.backend,
        config_mode=args.config_mode,
    )
    if args.out:
        print(f"wrote {args.out}")
    print(json.dumps(asdict(result), indent=2))


if __name__ == "__main__":
    main()
