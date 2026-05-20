"""Phase 6 identity bench: driver vs distributed identity resolution.

Driver mode: collect clusters to dict, call resolve_clusters directly.
Distributed mode: keep Ray Dataset, call resolve_identities_distributed.

Both modes write to the same Postgres backend. Emits JSON metrics for
the workflow runner to upload.

Usage:
    python scripts/bench_phase6_identity.py \\
        --mode distributed --rows 5000000 \\
        --dsn postgresql://postgres:bench@localhost:5432/postgres \\
        --out bench.json
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import time
from datetime import datetime
from pathlib import Path


def _peak_rss_gb() -> float:
    """Best-effort peak RSS in GB. Returns 0.0 on platforms where
    ``resource.getrusage`` isn't available (e.g. Windows)."""
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        # Linux: ru_maxrss is KB. macOS: bytes.
        if os.uname().sysname == "Darwin":  # pragma: no cover - mac path
            return usage.ru_maxrss / (1024**3)
        return usage.ru_maxrss / (1024**2)
    except Exception:  # pragma: no cover - windows
        return 0.0


def _synth_clusters(n_rows: int) -> tuple:
    """Build a synthetic (df, clusters, scored_pairs) tuple at requested scale.

    Pairs records into 2-member clusters. Predictable shape for bench
    repeatability.
    """
    import polars as pl

    members_a = list(range(0, n_rows, 2))
    members_b = list(range(1, n_rows, 2))[: len(members_a)]
    # Ensure equal length
    members_a = members_a[: len(members_b)]
    df = pl.DataFrame(
        {
            "__row_id__": list(range(n_rows)),
            "__source__": ["bench"] * n_rows,
            "name": [f"person_{i // 2}" for i in range(n_rows)],
            "email": [f"p{i // 2}@x.com" for i in range(n_rows)],
        }
    )
    clusters = {
        i: {
            "members": [a, b],
            "size": 2,
            "confidence": 0.95,
            "pair_scores": {(a, b): 0.95},
        }
        for i, (a, b) in enumerate(zip(members_a, members_b))
    }
    scored_pairs = [(a, b, 0.95) for a, b in zip(members_a, members_b)]
    return df, clusters, scored_pairs, len(clusters)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["driver", "distributed"], required=True)
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument(
        "--dsn",
        default=os.environ.get(
            "POSTGRES_URL",
            "postgresql://postgres:bench@localhost:5432/postgres",
        ),
    )
    ap.add_argument("--out", type=Path, default=Path("bench_phase6.json"))
    args = ap.parse_args()

    print(f"phase6-bench: mode={args.mode} rows={args.rows}")
    t0 = time.perf_counter()
    df, clusters, scored_pairs, n_clusters = _synth_clusters(args.rows)
    t_build = time.perf_counter() - t0

    # Pre-create schema -- both paths need it.
    from goldenmatch.identity.store import IdentityStore

    bootstrap = IdentityStore(backend="postgres", connection=args.dsn)
    bootstrap.close()

    run_name = f"bench-{args.mode}-{datetime.now().isoformat()}"
    t_id_start = time.perf_counter()

    if args.mode == "driver":
        from goldenmatch.identity.resolve import resolve_clusters
        from goldenmatch.identity.store import IdentityStore

        with IdentityStore(backend="postgres", connection=args.dsn) as store:
            summary = resolve_clusters(
                clusters, df, scored_pairs, "weighted", store, run_name=run_name,
            )
    else:
        from goldenmatch.distributed.identity import (
            resolve_identities_distributed,
        )

        summary = resolve_identities_distributed(
            clusters, df, scored_pairs, "weighted",
            dsn=args.dsn, run_name=run_name,
        )

    t_id_wall = time.perf_counter() - t_id_start
    total_wall = time.perf_counter() - t0

    out = {
        "mode": args.mode,
        "n_rows": args.rows,
        "n_clusters": n_clusters,
        "identity_wall_s": round(t_id_wall, 3),
        "build_wall_s": round(t_build, 3),
        "total_wall_s": round(total_wall, 3),
        "peak_rss_gb": round(_peak_rss_gb(), 3),
        "summary": summary.as_dict(),
        "identity_overhead_pct": (
            round(100 * t_id_wall / total_wall, 2) if total_wall else 0.0
        ),
    }
    args.out.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
