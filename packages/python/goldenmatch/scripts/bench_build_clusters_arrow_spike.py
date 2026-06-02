"""Measure-first spike: build_clusters_arrow (Arrow C) vs dict-shaped build_clusters.

#663-B decision spike. The dict-shaped build_clusters_native benched at 1.09x
(capped by per-cluster PyDict construction); build_clusters_arrow emits Arrow
buffers directly. This bench measures whether the Arrow path clears that
dict-floor end-to-end on the clustering step, at scale, with a fresh native build
(the in-tree _native.pyd is often stale and lacks build_clusters_arrow).

Compares pure Union-Find both ways (auto_split=False on the dict path so it's
apples-to-apples -- build_clusters_arrow does NOT auto-split), and sanity-checks
that the two produce identical cluster membership.

Run via the bench-build-clusters-arrow-spike workflow (large runner, fresh native).
Local smoke: python ... --np 100000  (will use the dict fallback if native is stale).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any


def _make_pairs_df(n_pairs_target: int):
    """Build a PAIR_STREAM_SCHEMA frame of ~n_pairs_target intra-cluster pairs.

    M clusters of size k=5 -> 10 pairs each. Deterministic, no RNG (Date.now /
    random are unavailable in some harnesses and we want reproducibility)."""
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


def _membership_from_dict(clusters: dict) -> set:
    out = set()
    for _cid, info in clusters.items():
        members = info.get("members") if isinstance(info, dict) else None
        if members and len(members) >= 2:
            out.add(frozenset(int(x) for x in members))
    return out


def _membership_from_frames(frames) -> set:
    # ClusterFrames.assignments: long form (cluster_id, member_id).
    out = set()
    df = frames.assignments
    if df.is_empty():
        return out
    for (cid,), grp in df.group_by(["cluster_id"], maintain_order=False):  # noqa: B007
        members = [int(x) for x in grp["member_id"].to_list()]
        if len(members) >= 2:
            out.add(frozenset(members))
    return out


def _peak_rss_mb() -> float:
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:
        return 0.0


def _bench_one(n_pairs: int, runs: int) -> dict[str, Any]:
    import polars as pl  # noqa: F401
    from goldenmatch.core.cluster import build_clusters, build_clusters_arrow_native

    pairs_df = _make_pairs_df(n_pairs)
    pairs_list = list(
        zip(
            pairs_df["id_a"].to_list(),
            pairs_df["id_b"].to_list(),
            pairs_df["score"].to_list(),
        )
    )
    actual_pairs = pairs_df.height

    def _dict_run():
        return build_clusters(pairs_list, auto_split=False)

    def _arrow_run():
        return build_clusters_arrow_native(pairs_df)

    # Warm + parity check once.
    dict_clusters = _dict_run()
    arrow_frames = _arrow_run()
    parity = _membership_from_dict(dict_clusters) == _membership_from_frames(arrow_frames)

    dict_t = []
    arrow_t = []
    for _ in range(runs):
        t0 = time.perf_counter(); _dict_run(); dict_t.append(time.perf_counter() - t0)
        t0 = time.perf_counter(); _arrow_run(); arrow_t.append(time.perf_counter() - t0)

    d = statistics.median(dict_t)
    a = statistics.median(arrow_t)
    return {
        "n_pairs": actual_pairs,
        "dict_s": d,
        "arrow_s": a,
        "speedup": (d / a) if a else float("nan"),
        "parity": parity,
        "peak_rss_mb": _peak_rss_mb(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--np", default="1000000,5000000",
                    help="Comma-separated target pair counts")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    nps = [int(x.strip()) for x in args.np.split(",") if x.strip()]
    runs = max(1, args.runs)

    from goldenmatch.core._native_loader import native_available, native_module
    print(f"native importable: {native_available()}", flush=True)
    m = native_module()
    print(f"build_clusters_arrow exposed: {bool(m) and hasattr(m, 'build_clusters_arrow')}", flush=True)

    results = []
    for n in nps:
        print(f"  target_pairs={n:,} ...", flush=True)
        try:
            row = _bench_one(n, runs)
            results.append(row)
            sp = f"{row['speedup']:.2f}x" if row["speedup"] == row["speedup"] else "n/a"
            print(f"    pairs={row['n_pairs']:,}  dict={row['dict_s']:.3f}s  "
                  f"arrow={row['arrow_s']:.3f}s  speedup={sp}  parity={row['parity']}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  target_pairs={n:,}  ERROR {type(exc).__name__}: {exc}", flush=True)

    lines = [
        "\n## bench-build-clusters-arrow-spike\n",
        f"| {'pairs':>12} | {'dict (s)':>10} | {'arrow (s)':>10} | {'speedup':>9} | {'parity':>7} | {'RSS MB':>8} |",
        f"| {'-'*12} | {'-'*10} | {'-'*10} | {'-'*9} | {'-'*7} | {'-'*8} |",
    ]
    for r in results:
        sp = f"{r['speedup']:.2f}x" if r["speedup"] == r["speedup"] else "n/a"
        lines.append(
            f"| {r['n_pairs']:>12,} | {r['dict_s']:>10.3f} | {r['arrow_s']:>10.3f} | "
            f"{sp:>9} | {str(r['parity']):>7} | {r['peak_rss_mb']:>8.1f} |"
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
