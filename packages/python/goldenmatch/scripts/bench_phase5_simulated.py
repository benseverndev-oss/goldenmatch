"""Run the Phase 5 streaming pipeline against an arbitrary Ray cluster.

Cluster-agnostic by design: connects via RAY_ADDRESS, doesn't start or
stop Ray. The workflow (or the user) is responsible for the cluster
lifecycle. This means the SAME script runs against:

  - A simulated 4-worker cluster inside one `large-new-64GB` runner
  - A real multi-node cluster (AWS, GCP, on-prem)

Outputs a JSON bench summary with per-stage walls + RSS.

Honest scoping (also surfaced in the workflow summary and docs):
results from the simulated single-host path share one NIC, one disk,
and the OS page cache. This is a regression check for the Phase 5
code path, NOT a Splink-Spark parity proof.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("bench_phase5_simulated")


def _peak_rss_gb() -> float:
    """Return the process peak RSS in GB, portable across Windows + Linux."""
    try:
        import resource  # noqa: PLC0415

        # On Linux ru_maxrss is in KB; on macOS in bytes. The runner is Linux.
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
    except ImportError:
        # Windows fallback (driver smoke tests only).
        try:
            import psutil  # noqa: PLC0415

            return psutil.Process().memory_info().rss / (1024 ** 3)
        except Exception:
            return 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--parquet",
        type=Path,
        required=True,
        help="Input parquet path (already on disk)",
    )
    ap.add_argument(
        "--rows",
        type=int,
        default=0,
        help="Expected row count for sanity logging (0 = skip check)",
    )
    ap.add_argument(
        "--identity",
        type=str,
        default="false",
        help="'true' to enable identity resolution; needs POSTGRES_URL",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("bench_phase5_simulated.json"),
    )
    args = ap.parse_args()

    identity_enabled = args.identity.lower() in ("true", "1", "yes")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
    )

    ray_address = os.environ.get("RAY_ADDRESS")
    if not ray_address:
        log.error(
            "RAY_ADDRESS unset -- this script requires a running Ray cluster",
        )
        return 1

    import ray  # noqa: PLC0415
    from goldenmatch.config.schemas import (  # noqa: PLC0415
        GoldenMatchConfig,
        IdentityConfig,
    )
    from goldenmatch.distributed.dataset import (  # noqa: PLC0415
        read_parquet_partitioned,
    )
    from goldenmatch.distributed.pipeline import (  # noqa: PLC0415
        run_dedupe_pipeline_distributed,
    )

    log.info("connecting to Ray at %s", ray_address)
    ray.init(address=ray_address, log_to_driver=False)
    cluster_resources = ray.cluster_resources()
    log.info("cluster resources: %s", cluster_resources)

    n_partitions = int(cluster_resources.get("CPU", 16))

    config = GoldenMatchConfig(backend="ray")
    if identity_enabled:
        postgres_url = os.environ.get("POSTGRES_URL")
        if not postgres_url:
            log.error("--identity true requires POSTGRES_URL env var")
            return 1
        config.identity = IdentityConfig(
            enabled=True,
            backend="postgres",
            connection=postgres_url,
        )

    log.info(
        "loading parquet %s with %d partitions",
        args.parquet,
        n_partitions,
    )
    ds = read_parquet_partitioned(str(args.parquet), n_partitions=n_partitions)

    rss_start_gb = _peak_rss_gb()
    t0 = time.perf_counter()

    os.environ["GOLDENMATCH_DISTRIBUTED_PIPELINE"] = "2"
    os.environ["GOLDENMATCH_ENABLE_DISTRIBUTED_RAY"] = "1"

    result = run_dedupe_pipeline_distributed(ds, config)

    wall_total = time.perf_counter() - t0
    rss_end_gb = _peak_rss_gb()

    summary = {
        "ray_address": ray_address,
        "cluster_resources": {
            k: float(v) for k, v in cluster_resources.items()
        },
        "parquet_path": str(args.parquet),
        "expected_rows": args.rows,
        "n_partitions": n_partitions,
        "identity_enabled": identity_enabled,
        "wall_total_s": round(wall_total, 2),
        "driver_rss_start_gb": round(rss_start_gb, 3),
        "driver_rss_end_gb": round(rss_end_gb, 3),
        "driver_rss_peak_gb": round(rss_end_gb, 3),  # ru_maxrss is high-water
        "identity_summary": (
            getattr(result, "identity_summary", None)
            if identity_enabled
            else None
        ),
        "scoping_note": (
            "Simulated single-host (single NIC, single disk, shared OS "
            "page cache). Regression check, NOT a Splink-Spark parity proof."
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2))
    log.info("wrote %s", args.out)
    log.info("wall_total_s=%s", summary["wall_total_s"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
