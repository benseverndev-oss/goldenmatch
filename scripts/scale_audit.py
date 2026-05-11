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
import gc
import json
import sys
import time
import tracemalloc
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

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
def _tracking(result: ScaleAuditResult, process: psutil.Process):
    """Wrap the whole run with tracemalloc + a global wall timer."""
    tracemalloc.start()
    t0 = time.perf_counter()
    rss0 = process.memory_info().rss
    try:
        yield
    finally:
        result.total_wall_seconds = time.perf_counter() - t0
        _, tm_peak = tracemalloc.get_traced_memory()
        result.peak_tracemalloc_mb = tm_peak / 1024 / 1024
        # Final RSS is approximate peak — RSS doesn't shrink immediately on
        # Linux; close enough for a "did this fit on a 64GB box" question.
        rss_now = process.memory_info().rss
        result.peak_rss_mb = max(rss_now, rss0) / 1024 / 1024
        tracemalloc.stop()


def run_audit(rows: int, dupe_rate: float = 0.15, fixture_dir: Path | None = None) -> ScaleAuditResult:
    """Run one audit pass against a synthetic fixture of the given size."""
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

    try:
        with _tracking(result, process):
            import polars as pl
            from goldenmatch.core.autoconfig import auto_configure_df
            from goldenmatch.core.pipeline import run_dedupe_df

            with _StageTimer("read_csv", result, process):
                df = pl.read_csv(fixture_path, encoding="utf8-lossy", ignore_errors=True)

            with _StageTimer("auto_configure", result, process):
                # The zero-config controller path — same one
                # `goldenmatch dedupe customers.csv` exercises with no flags.
                config = auto_configure_df(df)

            with _StageTimer("run_dedupe", result, process):
                pipeline_result = run_dedupe_df(df, config, output_clusters=True, auto_config=False)

            clusters = pipeline_result.get("clusters") or {}
            result.cluster_count = len(clusters)
    except Exception as exc:
        # Capturing the failure rather than re-raising — partial measurements
        # from earlier stages are still informative (often the OOM hits in a
        # specific stage and the others' timings are still valid).
        result.failed = f"{type(exc).__name__}: {exc}"

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
    ap.add_argument("--out", type=Path, help="write JSON result to this path")
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

    result = run_audit(rows=args.rows, dupe_rate=args.dupe_rate)
    blob = asdict(result)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(blob, indent=2))
        print(f"wrote {args.out}")
    print(json.dumps(blob, indent=2))


if __name__ == "__main__":
    main()
