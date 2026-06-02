"""SP-B residual bench. Measures cluster_frames_to_dict(build_cluster_frames(...))
wall + peak RSS -- the identity dict-rebuild cost SP-C removes. Recorded DATA,
not a gate.

This bench isolates TWO walls per scale point:
  1. ``build_walls``   -- ``build_cluster_frames(...)`` (the SP-B frames-out build,
     gate ``GOLDENMATCH_CLUSTER_FRAMES_OUT=1``).
  2. ``rebuild_walls`` -- ``cluster_frames_to_dict(frames)`` (the residual: rebuild
     the legacy ``dict[int, dict]`` from the two-frame columnar representation so
     today's identity stage can still consume it).

The residual (rebuild_walls) is the cost SP-C removes by teaching the identity
stage to read frames directly. There's a single measured operation per scale
point (no on/off variants -- this measures ONE thing), so there's NO parity
assert. The k=5 fixture (M clusters of size 5 -> 10 pairs each) has NO oversized
clusters, so this exercises the bulk-RSS axis (many small clusters). A fresh
native build in CI means the columnar build hits the real Arrow kernel.

Each scale point runs in its OWN subprocess (``--child``) so wall AND peak RSS
are clean. A warm run precedes the measured runs; the reported number is the
median wall over ``--runs`` repetitions.

Local smoke: python ... --np 50000 --runs 1  (resource is unavailable on Windows
so RSS is 0.0 -- fine).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _make_pairs_df(n_pairs_target: int):
    """M clusters of size k=5 -> 10 pairs each. Deterministic, no RNG."""
    import polars as pl

    k = 5
    per = k * (k - 1) // 2  # 10
    m = max(1, n_pairs_target // per)
    a_col: list[int] = []
    b_col: list[int] = []
    for c in range(m):
        base = c * k
        for i in range(k):
            for j in range(i + 1, k):
                a_col.append(base + i)
                b_col.append(base + j)
    s_col = [0.95] * len(a_col)
    return pl.DataFrame(
        {"id_a": a_col, "id_b": b_col, "score": s_col},
        schema={"id_a": pl.Int64, "id_b": pl.Int64, "score": pl.Float64},
    )


def _pairs_list_from_df(pairs_df) -> list[tuple[int, int, float]]:
    return list(
        zip(
            pairs_df["id_a"].to_list(),
            pairs_df["id_b"].to_list(),
            pairs_df["score"].to_list(),
        )
    )


def _peak_rss_mb() -> float:
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:
        return 0.0


def _run_child(n_pairs: int, runs: int) -> int:
    import polars as pl  # noqa: F401
    from goldenmatch.core.cluster import build_cluster_frames, cluster_frames_to_dict

    pairs_df = _make_pairs_df(n_pairs)
    pairs_list = _pairs_list_from_df(pairs_df)
    actual_pairs = pairs_df.height

    os.environ["GOLDENMATCH_CLUSTER_FRAMES_OUT"] = "1"

    def _build():
        return build_cluster_frames(
            pairs_list,
            all_ids=None,
            max_cluster_size=100,
            weak_cluster_threshold=0.3,
            auto_split=True,
        )

    frames = _build()  # warm build
    cluster_frames_to_dict(frames)  # warm rebuild

    build_walls: list[float] = []
    rebuild_walls: list[float] = []
    for _ in range(runs):
        t0 = time.perf_counter()
        frames = _build()
        build_walls.append(time.perf_counter() - t0)

        t1 = time.perf_counter()
        cluster_frames_to_dict(frames)
        rebuild_walls.append(time.perf_counter() - t1)

    print(json.dumps({
        "n_pairs": actual_pairs,
        "build_walls": build_walls,
        "rebuild_walls": rebuild_walls,
        "peak_rss_mb": _peak_rss_mb(),
    }), flush=True)
    return 0


def _bench_point(n: int, runs: int) -> dict[str, Any]:
    cmd = [
        sys.executable, os.path.abspath(__file__),
        "--child", "--np", str(n), "--runs", str(runs),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"child np={n} exited {proc.returncode}\n"
            f"--- stderr ---\n{proc.stderr.strip()}"
        )
    last_json = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            last_json = line
    if last_json is None:
        raise RuntimeError(
            f"child np={n} produced no JSON line\n"
            f"--- stdout ---\n{proc.stdout.strip()}"
        )
    return json.loads(last_json)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--np", default="1000000,5000000",
                    help="Comma-separated target pair counts")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--output", default=None)
    ap.add_argument("--child", action="store_true",
                    help="Internal: run a single scale point in this process")
    args = ap.parse_args()

    runs = max(1, args.runs)

    if args.child:
        nps = [int(x.strip()) for x in args.np.split(",") if x.strip()]
        n = nps[0] if nps else 1000000
        return _run_child(n, runs)

    nps = [int(x.strip()) for x in args.np.split(",") if x.strip()]

    from goldenmatch.core._native_loader import native_available, native_module
    print(f"native importable: {native_available()}", flush=True)
    m = native_module()
    print(f"build_clusters_arrow exposed: "
          f"{bool(m) and hasattr(m, 'build_clusters_arrow')}", flush=True)

    results = []
    for n in nps:
        print(f"  target_pairs={n:,} ...", flush=True)
        try:
            point = _bench_point(n, runs)
            build_s = statistics.median(point["build_walls"])
            rebuild_s = statistics.median(point["rebuild_walls"])
            row = {
                "n_pairs": point["n_pairs"],
                "build_s": build_s,
                "rebuild_s": rebuild_s,
                "peak_rss_mb": point["peak_rss_mb"],
            }
            results.append(row)
            print(f"    pairs={row['n_pairs']:,}  build={build_s:.3f}s  "
                  f"rebuild={rebuild_s:.3f}s  rss={row['peak_rss_mb']:.1f}MB",
                  flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  target_pairs={n:,}  ERROR {type(exc).__name__}: {exc}",
                  flush=True)
            results.append({"n_pairs": n, "error": str(exc)})

    lines = [
        "\n## bench-pipeline-frames-out\n",
        f"| {'pairs':>12} | {'build (s)':>10} | {'rebuild (s)':>11} | "
        f"{'peak RSS MB':>12} |",
        f"| {'-'*12} | {'-'*10} | {'-'*11} | {'-'*12} |",
    ]
    for r in results:
        if "error" in r:
            lines.append(
                f"| {r['n_pairs']:>12,} | {'ERROR':>10} | {'ERROR':>11} | "
                f"{'n/a':>12} |"
            )
            continue
        lines.append(
            f"| {r['n_pairs']:>12,} | {r['build_s']:>10.3f} | "
            f"{r['rebuild_s']:>11.3f} | {r['peak_rss_mb']:>12.1f} |"
        )
    table = "\n".join(lines)
    print(table, flush=True)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(table + "\n")
        except OSError:
            pass
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump({"results": results}, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
