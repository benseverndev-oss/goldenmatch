"""Measure-first harness: columnar drop-pair_scores build (gate ON) vs dict (OFF).

SP4 decision bench. The gated columnar ``build_clusters`` now returns
``pair_scores={}`` (no eager per-cluster dicts -- the SP1 bench loss). This bench
measures wall + peak RSS of ``build_clusters(pairs, ...)`` gate OFF (verbatim dict
path) vs ON (columnar, no eager dicts), at scale, with a fresh native build in CI
(so gate-ON hits the real Arrow kernel + reads confidence/min/avg off metadata).
If columnar wins net, flip ``GOLDENMATCH_COLUMNAR_CLUSTER_BUILD`` default-ON.

Each variant runs in its OWN subprocess (``--child {off|on}``) so wall AND peak
RSS are clean. Parity is asserted FIRST: gate-OFF vs gate-ON must be byte-identical
EXCEPT pair_scores (members as a frozenset; pair_scores dropped from the compare --
gate-ON carries {}; everything else strict). A perf number on a non-parity path is
meaningless, so parity failure short-circuits before the perf loop.

Local smoke: python ... --np 50000 --runs 1  (gate-ON uses the off-native columnar
path; resource is unavailable on Windows so RSS is 0.0 -- fine).
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


def _norm(cinfo: dict) -> dict:
    """SP4: members as a frozenset; pair_scores STRIPPED (gate-ON carries {}, the
    dict path carries real scores -- not compared here). Everything else strict."""
    out = {k: v for k, v in cinfo.items() if k not in ("members", "pair_scores", "_was_split")}
    out["members"] = frozenset(cinfo["members"])
    return out


def _run_child(variant: str, n_pairs: int, runs: int) -> int:
    os.environ["GOLDENMATCH_COLUMNAR_CLUSTER_BUILD"] = "1" if variant == "on" else "0"

    import polars as pl  # noqa: F401
    from goldenmatch.core.cluster import build_clusters

    pairs_df = _make_pairs_df(n_pairs)
    pairs_list = _pairs_list_from_df(pairs_df)
    actual_pairs = pairs_df.height

    build_clusters(pairs_list, auto_split=True)  # warm

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
        if on[cid]["pair_scores"] != {}:
            print(f"PARITY FAIL: cluster {cid} gate-ON pair_scores not empty",
                  flush=True)
            return False
    return True


def _bench_variant(variant: str, n: int, runs: int) -> dict[str, Any]:
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

    if args.child is not None:
        nps = [int(x.strip()) for x in args.np.split(",") if x.strip()]
        n = nps[0] if nps else 1000000
        return _run_child(args.child, n, runs)

    nps = [int(x.strip()) for x in args.np.split(",") if x.strip()]

    from goldenmatch.core._native_loader import native_available, native_module
    print(f"native importable: {native_available()}", flush=True)
    m = native_module()
    print(f"build_clusters_arrow exposed: "
          f"{bool(m) and hasattr(m, 'build_clusters_arrow')}", flush=True)

    parity_n = 2000
    print(f"parity check (np={parity_n:,}) ...", flush=True)
    if not _assert_parity(parity_n):
        print("ERROR: gate ON is NOT byte-identical (except pair_scores) to gate "
              "OFF; refusing to report perf on a non-parity path.", flush=True)
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
        "\n## bench-columnar-drop-pairscores\n",
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
