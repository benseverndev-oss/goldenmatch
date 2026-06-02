"""Measure-first harness: columnar cluster-build (gate ON) vs dict path (gate OFF).

SP2 decision bench for the Arrow-native columnar-cluster roadmap. Task 1 landed
the columnar core behind ``GOLDENMATCH_COLUMNAR_CLUSTER_BUILD`` (default OFF);
this bench measures wall + peak RSS of ``build_clusters(pairs, ...)`` with the
gate OFF (verbatim dict path) vs ON (columnar two-frame path), at scale, with a
fresh native build in CI (so gate-ON exercises the real Arrow UF kernel, not the
off-native fallback).

Each variant runs in its OWN subprocess (``--child {off|on}``) so wall AND peak
RSS are clean -- the off variant's allocations and the gate's in-process state
never leak into the on variant's numbers. The parent spawns both children per N,
parses their JSON, and assembles the markdown table.

Parity is asserted FIRST, in-process, on a small N: gate-OFF and gate-ON must
produce byte-identical dicts (members compared as a frozenset; everything else
strict). A perf number on a non-byte-identical path is meaningless, so parity
failure short-circuits before the perf loop.

Run via the bench-columnar-cluster-build workflow (large runner, fresh native).
Local smoke: python ... --np 50000 --runs 1  (gate-ON uses the off-native
columnar fallback; resource is unavailable on Windows so RSS is 0.0 -- fine).
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
    """Build a frame of ~n_pairs_target intra-cluster pairs.

    M clusters of size k=5 -> 10 pairs each. Deterministic, no RNG (we want
    reproducibility across runs and harnesses)."""
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
    """Convert the pair frame to the list[(int, int, float)] form
    ``build_clusters`` consumes (mirrors the spike's pairs_list zip)."""
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


def _norm(cinfo: dict) -> dict:
    """Normalise one cluster info dict for parity comparison: members as a
    frozenset (separate UF -> list order legitimately differs), drop _was_split.
    Mirrors tests/test_columnar_cluster_build_parity.py::_norm."""
    out = {k: v for k, v in cinfo.items() if k not in ("members", "_was_split")}
    out["members"] = frozenset(cinfo["members"])
    return out


def _run_child(variant: str, n_pairs: int, runs: int) -> int:
    """Child mode: set the gate, build pairs once, run build_clusters R times,
    print ONE JSON line with walls + peak RSS for this variant only."""
    os.environ["GOLDENMATCH_COLUMNAR_CLUSTER_BUILD"] = "1" if variant == "on" else "0"

    import polars as pl  # noqa: F401
    from goldenmatch.core.cluster import build_clusters

    pairs_df = _make_pairs_df(n_pairs)
    pairs_list = _pairs_list_from_df(pairs_df)
    actual_pairs = pairs_df.height

    # Warm once (kept out of the timed walls).
    build_clusters(pairs_list, auto_split=True)

    walls: list[float] = []
    for _ in range(runs):
        t0 = time.perf_counter()
        build_clusters(pairs_list, auto_split=True)
        walls.append(time.perf_counter() - t0)

    print(json.dumps({
        "variant": variant,
        "n_pairs": actual_pairs,
        "walls": walls,
        "peak_rss_mb": _peak_rss_mb(),
    }), flush=True)
    return 0


def _assert_parity(n_pairs: int) -> bool:
    """In-process parity on a small N: gate OFF vs ON must be byte-identical
    (members as a set, everything else strict). Returns True on parity."""
    from goldenmatch.core.cluster import build_clusters

    pairs_df = _make_pairs_df(n_pairs)
    pairs_list = _pairs_list_from_df(pairs_df)

    os.environ["GOLDENMATCH_COLUMNAR_CLUSTER_BUILD"] = "0"
    off = build_clusters(pairs_list, auto_split=True)

    os.environ["GOLDENMATCH_COLUMNAR_CLUSTER_BUILD"] = "1"
    on = build_clusters(pairs_list, auto_split=True)

    if on.keys() != off.keys():
        print(f"PARITY FAIL: cluster id sets differ "
              f"(off={len(off)} ids, on={len(on)} ids)", flush=True)
        return False
    for cid in off:
        if _norm(on[cid]) != _norm(off[cid]):
            print(f"PARITY FAIL: cluster {cid} differs:\n"
                  f"  off={off[cid]}\n  on={on[cid]}", flush=True)
            return False
    return True


def _bench_variant(variant: str, n: int, runs: int) -> dict[str, Any]:
    """Spawn a child subprocess for one variant + N. Raises on non-zero exit."""
    cmd = [
        sys.executable, os.path.abspath(__file__),
        "--child", variant, "--np", str(n), "--runs", str(runs),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"child {variant} np={n} exited {proc.returncode}\n"
            f"--- stderr ---\n{proc.stderr.strip()}"
        )
    # The child prints native-availability lines too; take the last JSON line.
    last_json = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            last_json = line
    if last_json is None:
        raise RuntimeError(
            f"child {variant} np={n} produced no JSON line\n"
            f"--- stdout ---\n{proc.stdout.strip()}"
        )
    return json.loads(last_json)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--np", default="1000000,5000000",
                    help="Comma-separated target pair counts")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--output", default=None)
    ap.add_argument("--child", choices=["off", "on"], default=None,
                    help="Internal: run a single variant in this process")
    args = ap.parse_args()

    runs = max(1, args.runs)

    # Child mode: one variant, one N, one JSON line.
    if args.child is not None:
        nps = [int(x.strip()) for x in args.np.split(",") if x.strip()]
        n = nps[0] if nps else 1000000
        return _run_child(args.child, n, runs)

    nps = [int(x.strip()) for x in args.np.split(",") if x.strip()]

    # Native availability (records whether gate-ON hit the real Arrow kernel).
    from goldenmatch.core._native_loader import native_available, native_module
    print(f"native importable: {native_available()}", flush=True)
    m = native_module()
    print(f"build_clusters_arrow exposed: "
          f"{bool(m) and hasattr(m, 'build_clusters_arrow')}", flush=True)

    # PARITY FIRST -- byte-identical or bust, before any perf measurement.
    parity_n = 2000
    print(f"parity check (np={parity_n:,}) ...", flush=True)
    if not _assert_parity(parity_n):
        print("ERROR: gate ON is NOT byte-identical to gate OFF; "
              "refusing to report perf on a non-parity path.", flush=True)
        return 1
    print("parity OK", flush=True)

    results = []
    for n in nps:
        print(f"  target_pairs={n:,} ...", flush=True)
        try:
            off = _bench_variant("off", n, runs)
            on = _bench_variant("on", n, runs)
            off_s = statistics.median(off["walls"])
            on_s = statistics.median(on["walls"])
            row = {
                "n_pairs": off["n_pairs"],
                "off_s": off_s,
                "on_s": on_s,
                "speedup": (off_s / on_s) if on_s else float("nan"),
                "off_rss_mb": off["peak_rss_mb"],
                "on_rss_mb": on["peak_rss_mb"],
            }
            results.append(row)
            sp = f"{row['speedup']:.2f}x" if row["speedup"] == row["speedup"] else "n/a"
            print(f"    pairs={row['n_pairs']:,}  off={off_s:.3f}s  "
                  f"on={on_s:.3f}s  speedup={sp}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  target_pairs={n:,}  ERROR {type(exc).__name__}: {exc}",
                  flush=True)
            results.append({"n_pairs": n, "error": str(exc)})

    lines = [
        "\n## bench-columnar-cluster-build\n",
        f"| {'pairs':>12} | {'off (s)':>10} | {'on (s)':>10} | "
        f"{'speedup':>9} | {'off RSS MB':>11} | {'on RSS MB':>11} |",
        f"| {'-'*12} | {'-'*10} | {'-'*10} | {'-'*9} | {'-'*11} | {'-'*11} |",
    ]
    for r in results:
        if "error" in r:
            lines.append(
                f"| {r['n_pairs']:>12,} | {'ERROR':>10} | {'ERROR':>10} | "
                f"{'n/a':>9} | {'n/a':>11} | {'n/a':>11} |"
            )
            continue
        sp = f"{r['speedup']:.2f}x" if r["speedup"] == r["speedup"] else "n/a"
        lines.append(
            f"| {r['n_pairs']:>12,} | {r['off_s']:>10.3f} | {r['on_s']:>10.3f} | "
            f"{sp:>9} | {r['off_rss_mb']:>11.1f} | {r['on_rss_mb']:>11.1f} |"
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
