"""Scaling harness for the SQLite identity resolve path (issue #2105).

The claim in #2105 is about SHAPE, not a single number: "cost per identity
grows 2.6x for a 4.1x scale increase", i.e. resolve was superlinear in the
identity count, and at 14M it OOM-killed a 64 GB box. So the metric that
matters here is **ms per identity across a scale ladder** — if that is flat,
resolve is linear in the identity graph; if it climbs, it is not.

Synthesises a frame shaped like the reported healthcare-provider run:

* a natural PK (``unique_id``), so record ids skip the fingerprint path
* ~2.14 records per multi-record cluster
* ~11% of input rows inside a multi-record cluster (the reported run resolved
  107,723 records out of 1M rows), so ``emit_singletons=False`` leaves the
  large majority of rows unreferenced
* the FULL pre-cluster scored-pair stream handed to ``resolve_clusters``, the
  way ``core/pipeline.py`` does

Each rung runs in its OWN subprocess so peak RSS is that rung's alone rather
than a process-wide high-water mark.

Arms (``--arms``):

* ``batched``    — current default (bounded prep + batched SQLite writes)
* ``autocommit`` — ``GOLDENMATCH_IDENTITY_SQLITE_BATCH=0``, i.e. the pre-#2105
  one-transaction-per-statement write path, with the bounded prep still in
  place. Isolates how much of the win is the write batching.

Usage:

    python scripts/bench_identity_resolve_scaling.py --ns 250000,1000000,4000000
    python scripts/bench_identity_resolve_scaling.py --emit-singletons --ns 100000,250000
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

COLS = [
    "npi", "email", "first_name", "last_name", "phone", "zip5",
    "address", "city", "state", "specialty", "org", "credential",
    "middle_name", "suffix",
]


def _peak_rss_gb() -> float:
    """Peak RSS of this process, in GB. 0.0 where unavailable (Windows)."""
    try:
        import resource
    except ImportError:
        return 0.0
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KB, macOS bytes.
    return rss / 1e6 if sys.platform != "darwin" else rss / 1e9


def _make_df(n: int):
    import polars as pl
    idx = pl.int_range(0, n, eager=True)
    data = {"unique_id": idx.cast(pl.Utf8)}
    for j, c in enumerate(COLS):
        data[c] = (idx + j).cast(pl.Utf8) + f"_{c}_padding_value"
    data["__row_id__"] = idx
    data["__source__"] = pl.Series(["src"] * n)
    return pl.DataFrame(data)


def _make_clusters(n: int, emit_singletons: bool) -> dict[int, dict]:
    clusters: dict[int, dict] = {}
    cid, row = 1, 0
    for i in range(max(1, n // 20)):
        size = 2 if i % 7 else 3
        members = list(range(row, row + size))
        row += size
        if row > n:
            break
        clusters[cid] = {
            "members": members, "size": size, "confidence": 0.85,
            "bottleneck_pair": None,
            "pair_scores": {(members[k], members[k + 1]): 0.9
                            for k in range(size - 1)},
        }
        cid += 1
    if emit_singletons:
        for r in range(row, n):
            clusters[cid] = {
                "members": [r], "size": 1, "confidence": 1.0,
                "bottleneck_pair": None, "pair_scores": {},
            }
            cid += 1
    return clusters


def _run_one(n: int, emit_singletons: bool) -> dict:
    """One rung, in-process. Invoked as a subprocess by the driver."""
    from goldenmatch.identity.resolve import resolve_clusters
    from goldenmatch.identity.store import IdentityStore

    df = _make_df(n)
    clusters = _make_clusters(n, emit_singletons)
    scored_pairs = [(i, i + 1, 0.8) for i in range(n * 2)]

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "identity.db")
        store = IdentityStore(backend="sqlite", path=path)
        t0 = time.perf_counter()
        summary = resolve_clusters(
            clusters, df, scored_pairs, "mk", store, run_name="bench",
            dataset="bench", source_pk_col="unique_id",
            emit_singletons=emit_singletons,
        )
        wall = time.perf_counter() - t0
        store.close()
        store_mb = os.path.getsize(path) / 1e6
    identities = max(summary.created, 1)
    return {
        "rows": n,
        "clusters": len(clusters),
        "identities": summary.created,
        "records_upserted": summary.records_upserted,
        "edges_added": summary.edges_added,
        "wall_s": round(wall, 3),
        "ms_per_identity": round(wall / identities * 1e3, 4),
        "peak_rss_gb": round(_peak_rss_gb(), 3),
        "store_mb": round(store_mb, 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", default="250000,1000000",
                    help="Row counts to sweep (comma-separated)")
    ap.add_argument("--arms", default="batched",
                    help="Comma-separated: batched, autocommit")
    ap.add_argument("--emit-singletons", action="store_true",
                    help="One identity per record (the schema default)")
    ap.add_argument("--output", default=None, help="Write JSON results here")
    # Internal: run a single rung and print JSON (used for the subprocess split).
    ap.add_argument("--_rung", type=int, default=None, help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args._rung is not None:
        print(json.dumps(_run_one(args._rung, args.emit_singletons)))
        return 0

    ns = [int(x) for x in args.ns.split(",") if x.strip()]
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    results: list[dict] = []

    for arm in arms:
        env = dict(os.environ)
        if arm == "autocommit":
            env["GOLDENMATCH_IDENTITY_SQLITE_BATCH"] = "0"
        else:
            env.pop("GOLDENMATCH_IDENTITY_SQLITE_BATCH", None)
        print(f"\n=== arm: {arm} (emit_singletons={args.emit_singletons}) ===")
        print(f"{'rows':>12} {'identities':>11} {'wall_s':>9} "
              f"{'ms/identity':>12} {'peak_rss_gb':>12} {'store_mb':>9}")
        for n in ns:
            cmd = [
                sys.executable, os.path.abspath(__file__),
                "--_rung", str(n), "--ns", "0", "--arms", arm,
            ]
            if args.emit_singletons:
                cmd.append("--emit-singletons")
            proc = subprocess.run(
                cmd, env=env, capture_output=True, text=True, check=False,
            )
            if proc.returncode != 0:
                # An OOM kill is a RESULT here, not a harness bug -- record it
                # and keep laddering so the report shows where the wall is.
                print(f"{n:>12,}  FAILED rc={proc.returncode} "
                      f"{proc.stderr.strip().splitlines()[-1:] or ''}")
                results.append({"arm": arm, "rows": n, "failed": True,
                                "returncode": proc.returncode})
                continue
            row = json.loads(proc.stdout.strip().splitlines()[-1])
            row["arm"] = arm
            results.append(row)
            print(f"{row['rows']:>12,} {row['identities']:>11,} "
                  f"{row['wall_s']:>9.2f} {row['ms_per_identity']:>12.4f} "
                  f"{row['peak_rss_gb']:>12.3f} {row['store_mb']:>9.2f}")

    # The #2105 shape check: ms/identity should be flat across the ladder.
    for arm in arms:
        rungs = [r for r in results if r.get("arm") == arm and not r.get("failed")]
        if len(rungs) >= 2:
            first, last = rungs[0], rungs[-1]
            growth = last["ms_per_identity"] / max(first["ms_per_identity"], 1e-9)
            scale = last["rows"] / max(first["rows"], 1)
            print(f"\n[{arm}] cost/identity grew {growth:.2f}x over a "
                  f"{scale:.1f}x row increase (flat == linear in the graph)")

    if args.output:
        with open(args.output, "w") as fh:
            json.dump({"emit_singletons": args.emit_singletons,
                       "results": results}, fh, indent=2)
        print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
