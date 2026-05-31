"""Day-3 bench: DataFusion vs bucket vs parallel on the same fixture.

Spike: docs/superpowers/specs/2026-05-30-datafusion-backend-spike-design.md
(gitignored)

Drives the full ``dedupe_df`` pipeline three times per N with
different ``config.backend`` values:

    parallel    — score_blocks_parallel (default, ThreadPoolExecutor + rapidfuzz cdist)
    bucket      — score_buckets (bypasses build_blocks; the production winner today)
    datafusion  — score_blocks_datafusion (this spike's contribution)

Same input frame, same matchkey, same blocking. Each backend runs 3
iterations; reports median wall + peak RSS + pair count.

Shape check (per spec Day 3): if 100K's datafusion wall is >2x
bucket's, stop and triage. If competitive, push to Day 4 (1M).

Run::

    pip install -e packages/python/goldenmatch[datafusion]
    python scripts/build_native.py   # required for datafusion + bucket native
    python packages/python/goldenmatch/scripts/bench_datafusion_vs_bucket.py
"""
from __future__ import annotations

import gc
import json
import statistics
import sys
import threading
import time
from pathlib import Path

import polars as pl
import psutil

# Reuse the surname-distributed person fixture from the autoconfig
# regression tests -- per memory/feedback_synthetic_surname_fixtures.md
# this is the shape that doesn't hang blocking+scoring for hours.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_autoconfig_regressions import _person_df  # noqa: E402

from goldenmatch import dedupe_df  # noqa: E402
from goldenmatch.config.schemas import (  # noqa: E402
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)


# Spike scope: single-field weighted jaro_winkler on last_name. Matches
# what the datafusion backend supports per
# datafusion_backend._validate_matchkey; the parallel + bucket
# backends accept it too, so all three measure the same workload.
def _make_config(backend: str) -> GoldenMatchConfig:
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
        backend=backend,
    )


class _RSSWatcher:
    """Background thread sampling process RSS every 50ms. Captures
    peak between ``start`` and ``stop``.

    Windows note: psutil's ``memory_info().rss`` returns the working
    set, which is the Windows equivalent of Linux RSS. Same metric
    across platforms for our purposes.
    """

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


def _run_once(df: pl.DataFrame, backend: str) -> dict:
    """Single dedupe call. Returns wall + peak RSS + pair/cluster counts."""
    cfg = _make_config(backend)
    gc.collect()
    watcher = _RSSWatcher()
    watcher.start()
    t0 = time.perf_counter()
    try:
        result = dedupe_df(df, config=cfg)
        wall = time.perf_counter() - t0
    except NotImplementedError as e:
        watcher.stop()
        return {"backend": backend, "error": f"NotImplementedError: {e}"}
    except Exception as e:  # noqa: BLE001 -- bench script wants to see ALL backend failures
        watcher.stop()
        return {"backend": backend, "error": f"{type(e).__name__}: {e}"}
    finally:
        watcher.stop()

    n_dupes = result.dupes.height if result.dupes is not None else 0
    n_clusters = len(result.clusters) if result.clusters else 0

    return {
        "backend": backend,
        "wall_s": wall,
        "peak_rss_mb": watcher.peak_bytes / (1024 * 1024),
        "duplicates": n_dupes,
        "clusters": n_clusters,
    }


def _bench_one_shape(n: int, n_iters: int = 3) -> dict:
    """For a given N, run all three backends n_iters times each."""
    print(f"\n=== n={n:,} (iters={n_iters} per backend) ===")
    df = _person_df(n)
    print(f"  fixture: height={df.height}, distinct_last_names={df['last_name'].n_unique()}")

    results: dict[str, list[dict]] = {}
    for backend in ("parallel", "bucket", "datafusion"):
        runs: list[dict] = []
        for i in range(n_iters):
            r = _run_once(df, backend)
            if "error" in r:
                print(f"  {backend}[{i}]: ERROR -- {r['error']}")
                runs.append(r)
                # Skip remaining iters of this backend -- they'll fail the same way
                break
            print(
                f"  {backend}[{i}]: wall={r['wall_s']:.3f}s  "
                f"peak_rss={r['peak_rss_mb']:.0f}MB  "
                f"dupes={r['duplicates']}  clusters={r['clusters']}"
            )
            runs.append(r)
        results[backend] = runs
    return {"n": n, "results": results}


def _summarize(shape: dict) -> dict:
    """Median wall + max peak RSS + pair-count consistency check."""
    summary: dict[str, dict] = {}
    for backend, runs in shape["results"].items():
        ok_runs = [r for r in runs if "error" not in r]
        if not ok_runs:
            summary[backend] = {"status": "FAILED", "error": runs[0].get("error")}
            continue
        walls = [r["wall_s"] for r in ok_runs]
        rsses = [r["peak_rss_mb"] for r in ok_runs]
        dupes = {r["duplicates"] for r in ok_runs}
        summary[backend] = {
            "status": "OK",
            "wall_median_s": statistics.median(walls),
            "peak_rss_mb": max(rsses),
            "duplicates": ok_runs[0]["duplicates"],
            "duplicates_consistent": len(dupes) == 1,
        }
    return summary


def _print_markdown_table(per_shape: list[dict]) -> None:
    print("\n\n## Bench summary\n")
    print("| n | backend | wall_median_s | peak_rss_MB | duplicates | status |")
    print("|---:|---|---:|---:|---:|---|")
    for shape in per_shape:
        n = shape["n"]
        for backend, s in shape["summary"].items():
            if s["status"] == "OK":
                print(
                    f"| {n:,} | {backend} | {s['wall_median_s']:.3f} | "
                    f"{s['peak_rss_mb']:.0f} | {s['duplicates']} | "
                    f"{'OK' if s['duplicates_consistent'] else 'INCONSISTENT'} |"
                )
            else:
                print(f"| {n:,} | {backend} | — | — | — | {s['status']} |")


def _decision_gate(per_shape: list[dict]) -> int:
    """Day-3 gate: at n=100K, if datafusion wall > 2x bucket wall,
    print a STOP recommendation and exit non-zero so CI can gate on
    it. Otherwise green-light Day 4 (1M)."""
    for shape in per_shape:
        if shape["n"] != 100_000:
            continue
        bucket = shape["summary"].get("bucket", {})
        df = shape["summary"].get("datafusion", {})
        if bucket.get("status") != "OK" or df.get("status") != "OK":
            print("\nGATE: at least one of bucket/datafusion failed at 100K. See errors above.")
            return 1
        ratio = df["wall_median_s"] / bucket["wall_median_s"]
        print(
            f"\nGATE @ 100K: datafusion_wall / bucket_wall = {ratio:.2f}x "
            f"(threshold for Day-3 continue: <= 2.0x)"
        )
        if ratio > 2.0:
            print("STOP: datafusion is >2x slower than bucket at 100K. "
                  "Triage before pushing to 1M (Day 4).")
            return 1
        print("CONTINUE: datafusion is within 2x of bucket. Push to Day 4 (1M).")
        return 0
    return 0


def main() -> int:
    per_shape: list[dict] = []
    for n in (10_000, 100_000):
        shape = _bench_one_shape(n, n_iters=3)
        shape["summary"] = _summarize(shape)
        per_shape.append(shape)

    _print_markdown_table(per_shape)

    out_dir = Path(__file__).resolve().parents[3] / ".profile_tmp" / "datafusion-bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "day3_results.json"
    out_path.write_text(json.dumps(per_shape, indent=2))
    print(f"\nJSON saved: {out_path}")

    return _decision_gate(per_shape)


if __name__ == "__main__":
    raise SystemExit(main())
