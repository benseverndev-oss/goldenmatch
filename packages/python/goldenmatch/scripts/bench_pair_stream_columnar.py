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
- Cluster output: byte-identical (verified by ``_partition_equal``
  in this script).

Today's Phase 1a sibling functions wrap-and-convert at the boundary,
so the columnar path at 5M is expected to be a small REGRESSION (wall
+5-10s for the list->DataFrame conversion, RSS roughly equal). The
kill criterion only gets met after Phase 1c collapses the legacy
inner loop into the columnar path. This bench script is here so
Phase 1c can measure against an established baseline.

Run via the bench-pair-stream-columnar workflow on large-new-64GB.
Don't run locally past 100K -- memory/feedback_avoid_full_suite_oom
applies (controller iteration at 100K+ OOMs Ben's dev box).
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
    """Build the block list once. Reused across both backends so the
    bench measures ONLY the scorer + cluster handoff.

    Inline the minimal ``__row_id__`` / ``__source__`` shape that
    blocker expects, instead of routing through the full pipeline
    helpers (those construct ``combined_lf`` inline inside
    ``_run_dedupe_pipeline`` and aren't extracted as a helper).
    """
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


def _bench_list_path(blocks: list, cfg: GoldenMatchConfig, all_ids: list[int]) -> dict:
    """Legacy list-based pair stream."""
    mk = cfg.matchkeys[0]
    matched: set[tuple[int, int]] = set()
    gc.collect()
    watcher = _RSSWatcher()
    watcher.start()
    t0 = time.perf_counter()
    pairs = score_blocks_parallel(blocks, mk, matched)
    score_wall = time.perf_counter() - t0
    t1 = time.perf_counter()
    clusters = build_clusters(pairs, all_ids=all_ids)
    cluster_wall = time.perf_counter() - t1
    watcher.stop()
    return {
        "path": "list",
        "score_wall_s": score_wall,
        "cluster_wall_s": cluster_wall,
        "total_wall_s": score_wall + cluster_wall,
        "peak_rss_mb": watcher.peak_bytes / (1024 * 1024),
        "n_pairs": len(pairs),
        "n_clusters": len(clusters),
        "clusters": clusters,  # for partition-equality check
    }


def _bench_columnar_path(blocks: list, cfg: GoldenMatchConfig, all_ids: list[int]) -> dict:
    """Phase 1a columnar pair stream (wrap-and-convert today)."""
    mk = cfg.matchkeys[0]
    matched: set[tuple[int, int]] = set()
    gc.collect()
    watcher = _RSSWatcher()
    watcher.start()
    t0 = time.perf_counter()
    pairs_df = score_blocks_columnar(blocks, mk, matched)
    score_wall = time.perf_counter() - t0
    t1 = time.perf_counter()
    clusters = build_clusters_columnar(pairs_df, all_ids=all_ids)
    cluster_wall = time.perf_counter() - t1
    watcher.stop()
    return {
        "path": "columnar",
        "score_wall_s": score_wall,
        "cluster_wall_s": cluster_wall,
        "total_wall_s": score_wall + cluster_wall,
        "peak_rss_mb": watcher.peak_bytes / (1024 * 1024),
        "n_pairs": pairs_df.height,
        "n_clusters": len(clusters),
        "clusters": clusters,
    }


def _partition_equal(a: dict[int, dict], b: dict[int, dict]) -> bool:
    """Compare cluster outputs as partitions (invariant under cluster_id
    relabeling)."""
    def partition(c: dict[int, dict]) -> frozenset[frozenset[int]]:
        return frozenset(
            frozenset(v.get("members", [])) for v in c.values()
        )
    return partition(a) == partition(b)


def _bench_one_shape(n: int, n_iters: int = 3) -> dict:
    print(f"\n=== n={n:,} (iters={n_iters} per path) ===", flush=True)
    df = realistic_person_df(n)
    if "__row_id__" not in df.columns:
        df = df.with_row_index(name="__row_id__").with_columns(
            pl.col("__row_id__").cast(pl.Int64),
        )
    print(f"  fixture: height={df.height}, distinct_last_names={df['last_name'].n_unique()}",
          flush=True)

    cfg = _make_config()
    blocks, all_ids = _prepare_blocks(df, cfg)
    print(f"  blocks: {len(blocks)}", flush=True)

    results: dict[str, list[dict]] = {"list": [], "columnar": []}
    for i in range(n_iters):
        r_list = _bench_list_path(blocks, cfg, all_ids)
        print(
            f"  list[{i}]: score={r_list['score_wall_s']:.2f}s  "
            f"cluster={r_list['cluster_wall_s']:.2f}s  "
            f"total={r_list['total_wall_s']:.2f}s  "
            f"rss={r_list['peak_rss_mb']:.0f}MB  "
            f"pairs={r_list['n_pairs']:,}  clusters={r_list['n_clusters']:,}",
            flush=True,
        )
        results["list"].append(r_list)
        r_col = _bench_columnar_path(blocks, cfg, all_ids)
        print(
            f"  columnar[{i}]: score={r_col['score_wall_s']:.2f}s  "
            f"cluster={r_col['cluster_wall_s']:.2f}s  "
            f"total={r_col['total_wall_s']:.2f}s  "
            f"rss={r_col['peak_rss_mb']:.0f}MB  "
            f"pairs={r_col['n_pairs']:,}  clusters={r_col['n_clusters']:,}",
            flush=True,
        )
        results["columnar"].append(r_col)

    # Partition equality check across the first iteration of each path.
    partition_ok = _partition_equal(
        results["list"][0]["clusters"],
        results["columnar"][0]["clusters"],
    )
    print(f"  partition equal: {partition_ok}", flush=True)

    # Drop the cluster dict from JSON output (huge + not useful in
    # aggregate). Keep the summary metrics.
    for path in ("list", "columnar"):
        for r in results[path]:
            r.pop("clusters", None)

    return {
        "n": n,
        "results": results,
        "partition_equal": partition_ok,
    }


def _summarize(per_shape: list[dict]) -> None:
    print("\n\n## Pair stream bench summary", flush=True)
    print("", flush=True)
    print("| n | path | total_wall_s | peak_rss_MB | n_pairs | n_clusters | partition_eq |",
          flush=True)
    print("|---:|---|---:|---:|---:|---:|---|", flush=True)
    for shape in per_shape:
        n = shape["n"]
        for path in ("list", "columnar"):
            runs = shape["results"][path]
            wall = statistics.median(r["total_wall_s"] for r in runs)
            rss = max(r["peak_rss_mb"] for r in runs)
            pairs = runs[0]["n_pairs"]
            clusters = runs[0]["n_clusters"]
            print(
                f"| {n:,} | {path} | {wall:.2f} | {rss:.0f} | {pairs:,} | "
                f"{clusters:,} | {'OK' if shape['partition_equal'] else 'MISMATCH'} |",
                flush=True,
            )


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--shapes", type=int, nargs="+", default=[100_000, 1_000_000, 5_000_000],
        help="Row counts to bench. Default: 100K, 1M, 5M.",
    )
    p.add_argument("--iters", type=int, default=3, help="Iterations per (n, path).")
    args = p.parse_args()

    per_shape: list[dict] = []
    for n in args.shapes:
        per_shape.append(_bench_one_shape(n, n_iters=args.iters))

    _summarize(per_shape)

    out = Path(__file__).resolve().parents[3] / ".profile_tmp" / "pair-stream-bench"
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(per_shape, indent=2))
    print(f"\nJSON saved: {out / 'results.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
