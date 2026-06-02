"""Measure-first bench: batch_fingerprints vs per-row record_fingerprint loop.

Gate: before flipping GOLDENMATCH_IDENTITY_BATCH_FINGERPRINT default to "1",
measure whether the batch path (Arrow kernel or dict-bulk fallback) actually
wins end-to-end inside resolve_clusters at the row counts we care about.

Two modes, selected automatically:
  end-to-end  -- calls resolve_clusters with the gate on vs off and times the
                 full fingerprint + record-id derivation + store-write loop.
                 Preferred: isolates the same code path the gate controls.
  fingerprint-only -- if resolve_clusters setup is unavailable, times just
                 batch_fingerprints(df) vs the per-row list comprehension.
                 Labelled clearly in output.

Usage:
    uv run python packages/python/goldenmatch/scripts/bench_identity_fingerprint.py \\
        [--ns 1000000,5000000] [--output bench.json]

Smoke (local, ~1s):
    .venv/Scripts/python.exe packages/python/goldenmatch/scripts/bench_identity_fingerprint.py \\
        --ns 1000 --output ./_bench_smoke.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import polars as pl


# ---------------------------------------------------------------------------
# RSS helper (Linux only; graceful on Windows)
# ---------------------------------------------------------------------------

def _peak_rss_mb() -> float:
    """Best-effort peak RSS in MB. Returns 0.0 on Windows / unsupported."""
    try:
        import resource  # noqa: PLC0415 -- conditional import
        usage = resource.getrusage(resource.RUSAGE_SELF)
        if sys.platform == "darwin":
            return usage.ru_maxrss / (1024 * 1024)
        return usage.ru_maxrss / 1024  # Linux: KB
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Synthetic person DataFrame (NO source PK so every row fingerprints)
# ---------------------------------------------------------------------------

_FIRST = [
    "Alex", "Blair", "Casey", "Dana", "Eli", "Finley", "Gray", "Harper",
    "Indigo", "Jamie", "Kendall", "Logan", "Morgan", "Noel", "Oakley",
    "Parker", "Quinn", "Riley", "Sage", "Taylor",
]
_LAST = [
    "Smith", "Jones", "Williams", "Brown", "Davis", "Miller", "Wilson",
    "Moore", "Taylor", "Anderson", "Thomas", "Jackson", "White", "Harris",
    "Martin", "Thompson", "Garcia", "Martinez", "Robinson", "Clark",
]


def _make_df(n: int) -> pl.DataFrame:
    import random

    import polars as pl

    rng = random.Random(42)
    first_names = [rng.choice(_FIRST) for _ in range(n)]
    last_names = [rng.choice(_LAST) for _ in range(n)]
    return pl.DataFrame({
        "__row_id__": list(range(n)),
        "__source__": ["bench"] * n,
        "first_name": first_names,
        "last_name": last_names,
        "email": [f"{fn.lower()}.{ln.lower()}.{i}@example.com"
                  for i, (fn, ln) in enumerate(zip(first_names, last_names))],
        "city": [rng.choice(["Springfield", "Shelbyville", "Capital City", "Ogdenville"]) for _ in range(n)],
        "zip": [f"{rng.randint(10000, 99999):05d}" for _ in range(n)],
    })


# ---------------------------------------------------------------------------
# Cluster helpers: N singleton clusters (one per row -- trivial topology,
# forces resolve_clusters to fingerprint every row without any store-absorb
# shortcuts from previous runs).
# ---------------------------------------------------------------------------

def _singleton_clusters(n: int) -> dict[int, dict[str, Any]]:
    return {
        i: {
            "members": [i],
            "size": 1,
            "oversized": False,
            "pair_scores": {},
            "confidence": 1.0,
            "cluster_quality": "strong",
        }
        for i in range(n)
    }


# ---------------------------------------------------------------------------
# End-to-end bench via resolve_clusters
# ---------------------------------------------------------------------------

def _bench_end_to_end(n: int, runs: int) -> dict[str, Any]:
    """Time resolve_clusters with gate ON vs OFF. Returns per-run stats."""
    from goldenmatch.identity import IdentityStore, resolve_clusters

    df = _make_df(n)
    clusters = _singleton_clusters(n)

    def run_once(gate_value: str) -> float:
        os.environ["GOLDENMATCH_IDENTITY_BATCH_FINGERPRINT"] = gate_value
        with tempfile.TemporaryDirectory() as td:
            store = IdentityStore(path=str(Path(td) / "identity.db"))
            try:
                t0 = time.perf_counter()
                resolve_clusters(
                    clusters, df, [], None, store,
                    run_name="bench", source_pk_col=None,
                )
                return time.perf_counter() - t0
            finally:
                store.close()

    walls_off = [run_once("0") for _ in range(runs)]
    walls_on = [run_once("1") for _ in range(runs)]

    med_off = statistics.median(walls_off)
    med_on = statistics.median(walls_on)
    speedup = med_off / med_on if med_on > 0 else float("nan")

    return {
        "n": n,
        "mode": "end-to-end",
        "no_pk_fraction": 1.0,
        "wall_off_s": med_off,
        "wall_on_s": med_on,
        "speedup": speedup,
        "peak_rss_mb": _peak_rss_mb(),
        "runs": runs,
    }


# ---------------------------------------------------------------------------
# Fingerprint-only microbench (fallback if resolve import fails)
# ---------------------------------------------------------------------------

def _bench_fingerprint_only(n: int, runs: int) -> dict[str, Any]:
    """Time batch_fingerprints vs per-row loop directly."""
    from goldenmatch.core._hashing import record_fingerprint
    from goldenmatch.identity.fingerprint_batch import (
        _canonical_payload,
        batch_fingerprints,
    )

    df = _make_df(n)
    rows_dicts = df.to_dicts()

    def _per_row():
        return [
            record_fingerprint(_canonical_payload({k: v for k, v in r.items() if not k.startswith("__")}))
            for r in rows_dicts
        ]

    def _batch():
        return batch_fingerprints(df)

    walls_per_row = []
    for _ in range(runs):
        t0 = time.perf_counter()
        _per_row()
        walls_per_row.append(time.perf_counter() - t0)

    walls_batch = []
    for _ in range(runs):
        t0 = time.perf_counter()
        _batch()
        walls_batch.append(time.perf_counter() - t0)

    med_off = statistics.median(walls_per_row)
    med_on = statistics.median(walls_batch)
    speedup = med_off / med_on if med_on > 0 else float("nan")

    return {
        "n": n,
        "mode": "fingerprint-only",
        "no_pk_fraction": 1.0,
        "wall_off_s": med_off,
        "wall_on_s": med_on,
        "speedup": speedup,
        "peak_rss_mb": _peak_rss_mb(),
        "runs": runs,
    }


# ---------------------------------------------------------------------------
# Markdown table
# ---------------------------------------------------------------------------

def _print_table(results: list[dict[str, Any]], mode: str) -> None:
    header = (
        f"\n### bench-identity-fingerprint  [{mode}]\n\n"
        f"| {'N':>10} | {'per-row (s)':>12} | {'batch (s)':>10} | {'speedup':>9} | "
        f"{'no-PK %':>8} | {'peak RSS MB':>12} |\n"
        f"| {'-'*10} | {'-'*12} | {'-'*10} | {'-'*9} | {'-'*8} | {'-'*12} |"
    )
    print(header)
    for r in results:
        speedup_s = f"{r['speedup']:.2f}x" if r['speedup'] == r['speedup'] else "n/a"
        print(
            f"| {r['n']:>10,} | {r['wall_off_s']:>12.3f} | {r['wall_on_s']:>10.3f} | "
            f"{speedup_s:>9} | {r['no_pk_fraction']*100:>7.0f}% | "
            f"{r['peak_rss_mb']:>12.1f} |"
        )
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="bench-identity-fingerprint: batch vs per-row speedup"
    )
    ap.add_argument("--ns", default="1000000,5000000",
                    help="Comma-separated row counts (default: 1000000,5000000)")
    ap.add_argument("--runs", type=int, default=3,
                    help="Repetitions per measurement (median reported, default: 3)")
    ap.add_argument("--output", default=None,
                    help="JSON output path (optional)")
    args = ap.parse_args()

    ns = [int(x.strip()) for x in args.ns.split(",") if x.strip()]
    runs = max(1, args.runs)

    # Choose mode: prefer end-to-end, fall back to fingerprint-only.
    try:
        from goldenmatch.identity import IdentityStore, resolve_clusters  # noqa: F401
        mode = "end-to-end"
        bench_fn = _bench_end_to_end
    except Exception as exc:
        print(f"[bench] WARNING: resolve_clusters unavailable ({exc!r}); falling back to fingerprint-only mode", flush=True)
        mode = "fingerprint-only"
        bench_fn = _bench_fingerprint_only

    print(f"ns={ns}  runs={runs}  mode={mode}", flush=True)

    from goldenmatch.core._native_loader import native_available
    print(f"native ext importable: {native_available()}", flush=True)

    results = []
    for n in ns:
        print(f"  N={n:,} ...", flush=True)
        try:
            row = bench_fn(n, runs)
            results.append(row)
            speedup_s = f"{row['speedup']:.2f}x" if row['speedup'] == row['speedup'] else "n/a"
            print(f"    per-row={row['wall_off_s']:.3f}s  batch={row['wall_on_s']:.3f}s  speedup={speedup_s}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  N={n:,}  ERROR {type(exc).__name__}: {exc}", flush=True)

    _print_table(results, mode)

    # Append to GITHUB_STEP_SUMMARY if set.
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(f"\n## bench-identity-fingerprint [{mode}]\n\n")
                fh.write(
                    f"| {'N':>10} | {'per-row (s)':>12} | {'batch (s)':>10} | "
                    f"{'speedup':>9} | {'no-PK %':>8} | {'peak RSS MB':>12} |\n"
                )
                fh.write(
                    f"| {'-'*10} | {'-'*12} | {'-'*10} | {'-'*9} | {'-'*8} | {'-'*12} |\n"
                )
                for r in results:
                    speedup_s = f"{r['speedup']:.2f}x" if r['speedup'] == r['speedup'] else "n/a"
                    fh.write(
                        f"| {r['n']:>10,} | {r['wall_off_s']:>12.3f} | {r['wall_on_s']:>10.3f} | "
                        f"{speedup_s:>9} | {r['no_pk_fraction']*100:>7.0f}% | "
                        f"{r['peak_rss_mb']:>12.1f} |\n"
                    )
        except OSError as exc:
            print(f"[bench] WARNING: could not write GITHUB_STEP_SUMMARY: {exc}", flush=True)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump({"mode": mode, "results": results}, fh, indent=2)
        print(f"[bench] JSON written to {out_path}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
