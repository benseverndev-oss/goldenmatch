"""Bench: list-based vs columnar pair stream (Arrow roadmap Phase 1).

Measures the kill criterion for issue #623 (Phase 1 of the
Arrow-native roadmap, ``docs/superpowers/specs/2026-05-31-arrow-native-roadmap.md``,
gitignored).

The Phase 1 spec sets a binding kill criterion at 5M rows on
``large-new-64GB``:

- Wall: pair stream handoff (scorer -> cluster) <= 50% of pre-Phase-1
  wall (which is whatever the legacy ``list[tuple]`` path takes today).
- Peak RSS: pair-stream segment RSS <= 25% of pre-Phase-1 (legacy
  list at 5M holds ~8 GB of Python tuples for 200M pairs).
- Cluster output: byte-identical (verified separately by
  ``tests/test_pair_stream_columnar_parity.py``).

## Subprocess isolation (2026-05-31, fixes OOM-cascade in run 26715526689)

The first dispatch on the realistic-person fixture surfaced a runner
OOM at the 1M shape: the ``list`` path produced ~131M pairs / ~48 GB
peak RSS, leaving no headroom for the ``columnar`` path to even
start before the kernel SIGTERM'd the process. We never captured
columnar's measurement, and the entire bench exited non-zero.

Fix: each (shape, path, iter) combination now runs in its OWN
Python subprocess. The outer orchestrator spawns workers, collects
JSON results from stdout, and continues past OOMs (records them as
error rows in the summary table) instead of crashing the whole
bench. Each subprocess gets a fresh Python heap on entry; RSS from
the previous run is fully reclaimed before the next path starts.

Run via the bench-pair-stream-columnar workflow on large-new-64GB.
Don't run locally past 100K -- memory/feedback_avoid_full_suite_oom
applies.
"""
from __future__ import annotations

import argparse
import gc
import json
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

import polars as pl
import psutil

# Reuse the realistic-person fixture from Arrow roadmap Phase 0.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from fixtures.realistic_person import realistic_person_df  # noqa: E402
from goldenmatch.config.schemas import (  # noqa: E402
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.blocker import build_blocks  # noqa: E402
from goldenmatch.core.cluster import (  # noqa: E402
    build_clusters,
    build_clusters_columnar,
)
from goldenmatch.core.scorer import (  # noqa: E402
    score_blocks_columnar,
    score_blocks_parallel,
)


def _make_config() -> GoldenMatchConfig:
    """Single-field weighted jaro_winkler on last_name; soundex
    blocking. Matches the Phase 0 fixture's matchkey shape."""
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="last_name_fuzzy",
                type="weighted",
                fields=[MatchkeyField(field="last_name", scorer="jaro_winkler", weight=1.0)],
                threshold=0.85,
            )
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["last_name"], transforms=["soundex"])],
        ),
    )


class _RSSWatcher:
    """Sample process RSS every 50ms in a background thread; track peak."""

    def __init__(self, interval_s: float = 0.05) -> None:
        self._interval = interval_s
        self._proc = psutil.Process()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peak_bytes: int = 0

    def start(self) -> None:
        self.peak_bytes = self._proc.memory_info().rss
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                rss = self._proc.memory_info().rss
                if rss > self.peak_bytes:
                    self.peak_bytes = rss
            except psutil.Error:
                break
            self._stop.wait(self._interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)


def _prepare_blocks(df: pl.DataFrame, cfg: GoldenMatchConfig) -> tuple[list, list[int]]:
    """Build the block list. Inline the minimal ``__row_id__`` /
    ``__source__`` shape that ``blocker`` expects."""
    prepped = df.with_columns(
        pl.lit("fixture").alias("__source__"),
    )
    if "__row_id__" not in prepped.columns:
        prepped = prepped.with_row_index(name="__row_id__").with_columns(
            pl.col("__row_id__").cast(pl.Int64),
        )
    blocks = build_blocks(prepped.lazy(), cfg.blocking)
    all_ids = prepped["__row_id__"].to_list()
    return blocks, all_ids


# ── Worker mode (single (n, path) measurement) ──────────────────────


def _worker_main(n: int, path: str) -> int:
    """Single (n, path) measurement. Emits one JSON line to stdout
    with wall + RSS + counts, exits 0. Outer orchestrator captures
    the JSON.

    Worker process scope: one fixture build + one block build + one
    scorer call + one cluster call. Fresh Python heap on entry so
    RSS reflects only this measurement, not whatever the previous
    path leaked.
    """
    df = realistic_person_df(n)
    if "__row_id__" not in df.columns:
        df = df.with_row_index(name="__row_id__").with_columns(
            pl.col("__row_id__").cast(pl.Int64),
        )
    cfg = _make_config()
    blocks, all_ids = _prepare_blocks(df, cfg)
    mk = cfg.matchkeys[0]
    matched: set[tuple[int, int]] = set()

    gc.collect()
    watcher = _RSSWatcher()
    watcher.start()
    t0 = time.perf_counter()

    if path == "list":
        pairs = score_blocks_parallel(blocks, mk, matched)
        score_wall = time.perf_counter() - t0
        t1 = time.perf_counter()
        clusters = build_clusters(pairs, all_ids=all_ids)
        cluster_wall = time.perf_counter() - t1
        n_pairs = len(pairs)
        n_clusters = len(clusters)
    elif path == "columnar":
        pairs_df = score_blocks_columnar(blocks, mk, matched)
        score_wall = time.perf_counter() - t0
        t1 = time.perf_counter()
        clusters = build_clusters_columnar(pairs_df, all_ids=all_ids)
        cluster_wall = time.perf_counter() - t1
        n_pairs = pairs_df.height
        n_clusters = len(clusters)
    else:
        print(f"unknown path: {path}", file=sys.stderr, flush=True)
        return 2

    watcher.stop()
    result = {
        "n": n,
        "path": path,
        "score_wall_s": score_wall,
        "cluster_wall_s": cluster_wall,
        "total_wall_s": score_wall + cluster_wall,
        "peak_rss_mb": watcher.peak_bytes / (1024 * 1024),
        "n_pairs": n_pairs,
        "n_clusters": n_clusters,
    }
    # Emit on a magic prefix so the parent can extract it even if
    # other stdout chatter (Polars warnings, etc.) bleeds in.
    print(f"__BENCH_JSON__{json.dumps(result)}", flush=True)
    return 0


# ── Orchestrator mode (spawns workers per (n, path, iter)) ──────────


def _run_worker(n: int, path: str, script_path: Path) -> dict | None:
    """Spawn one (n, path) measurement as a subprocess. Returns the
    parsed JSON result, or None on OOM / non-zero exit / parse failure.

    The worker exits cleanly on success; OOM manifests as exit code
    -9 / 137 / 143 (SIGKILL or SIGTERM from the kernel). We log and
    move on -- the next worker gets a fresh heap.
    """
    cmd = [sys.executable, str(script_path), "--worker", str(n), path]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"  {path}@{n:,}: TIMEOUT after 3600s", flush=True)
        return None

    if proc.returncode != 0:
        # Most common: OOM-killed by the kernel. exit_code 137 = SIGKILL,
        # 143 = SIGTERM (the runner's grace-period kill).
        signal_hint = ""
        if proc.returncode in (137, -9):
            signal_hint = " (likely OOM-killed)"
        elif proc.returncode == 143:
            signal_hint = " (SIGTERM, likely OOM or runner timeout)"
        print(
            f"  {path}@{n:,}: exit {proc.returncode}{signal_hint}",
            flush=True,
        )
        # Print last few stderr lines for diagnostic context.
        if proc.stderr:
            for line in proc.stderr.strip().splitlines()[-3:]:
                print(f"    stderr: {line}", flush=True)
        return None

    # Extract the JSON line from stdout.
    for line in proc.stdout.splitlines():
        if line.startswith("__BENCH_JSON__"):
            return json.loads(line[len("__BENCH_JSON__"):])

    print(f"  {path}@{n:,}: no JSON in worker output", flush=True)
    return None


def _bench_one_shape(n: int, n_iters: int, script_path: Path) -> dict:
    """For each path, run n_iters subprocesses sequentially.

    Subprocess isolation: even if the ``list`` path OOMs at this n,
    the ``columnar`` path still gets its own fresh process with the
    full 64GB available. Same in reverse.
    """
    print(f"\n=== n={n:,} (iters={n_iters} per path, subprocess-isolated) ===",
          flush=True)
    results: dict[str, list[dict]] = {"list": [], "columnar": []}
    for path in ("list", "columnar"):
        for i in range(n_iters):
            r = _run_worker(n, path, script_path)
            if r is None:
                results[path].append({"path": path, "error": "worker_failed"})
                # Don't keep retrying: if iter 0 OOMs, iter 1 will too.
                break
            print(
                f"  {path}[{i}]: score={r['score_wall_s']:.2f}s  "
                f"cluster={r['cluster_wall_s']:.2f}s  "
                f"total={r['total_wall_s']:.2f}s  "
                f"rss={r['peak_rss_mb']:.0f}MB  "
                f"pairs={r['n_pairs']:,}  clusters={r['n_clusters']:,}",
                flush=True,
            )
            results[path].append(r)
    return {"n": n, "results": results}


def _summarize(per_shape: list[dict]) -> None:
    print("\n\n## Pair stream bench summary", flush=True)
    print("", flush=True)
    print(
        "| n | path | total_wall_s | peak_rss_MB | n_pairs | n_clusters | status |",
        flush=True,
    )
    print("|---:|---|---:|---:|---:|---:|---|", flush=True)
    for shape in per_shape:
        n = shape["n"]
        for path in ("list", "columnar"):
            runs = shape["results"][path]
            ok = [r for r in runs if "error" not in r]
            if not ok:
                print(
                    f"| {n:,} | {path} | — | — | — | — | "
                    f"{runs[0].get('error', 'failed') if runs else 'no_runs'} |",
                    flush=True,
                )
                continue
            wall = statistics.median(r["total_wall_s"] for r in ok)
            rss = max(r["peak_rss_mb"] for r in ok)
            pairs = ok[0]["n_pairs"]
            clusters = ok[0]["n_clusters"]
            print(
                f"| {n:,} | {path} | {wall:.2f} | {rss:.0f} | {pairs:,} | "
                f"{clusters:,} | OK |",
                flush=True,
            )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--shapes", type=int, nargs="+", default=[100_000, 1_000_000, 5_000_000],
        help="Row counts to bench. Default: 100K, 1M, 5M.",
    )
    p.add_argument("--iters", type=int, default=3, help="Iterations per (n, path).")
    p.add_argument(
        "--worker", nargs=2, metavar=("N", "PATH"), default=None,
        help="Internal: run a single (n, path) measurement and emit "
             "JSON on stdout. Used by the orchestrator to spawn isolated "
             "subprocesses.",
    )
    args = p.parse_args()

    script_path = Path(__file__).resolve()

    if args.worker is not None:
        n_str, path = args.worker
        return _worker_main(int(n_str), path)

    per_shape: list[dict] = []
    for n in args.shapes:
        per_shape.append(_bench_one_shape(n, args.iters, script_path))

    _summarize(per_shape)

    out = Path(__file__).resolve().parents[3] / ".profile_tmp" / "pair-stream-bench"
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(per_shape, indent=2))
    print(f"\nJSON saved: {out / 'results.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
