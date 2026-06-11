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
    ap.add_argument(
        "--block-shuffle",
        choices=["0", "1"],
        default="0",
        help=(
            "1 = recall-complete leg: enables GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE "
            "and routes clustering to randomized_contraction WCC. Default 0 = "
            "baseline (per-partition scoring + local_cc_assignments)."
        ),
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

    if identity_enabled:
        # The Phase 5 streaming pipeline auto-configures internally and
        # doesn't accept an injected IdentityConfig today. Identity-on-
        # simulated-bench would require plumbing IdentityConfig through
        # run_dedupe_pipeline_distributed; tracked separately.
        log.error(
            "--identity true is not yet supported on the simulated path. "
            "Use `bench-phase6-identity` for the driver-vs-distributed "
            "identity comparison; this bench is Phase 5 streaming only.",
        )
        return 1

    # Ray workers start in a different cwd than the driver (typically
    # /tmp/ray/session_*/...). A relative parquet path resolves wrong
    # on the worker side and the open fails with FileNotFoundError.
    # Resolve to absolute on the driver before handing off to Ray.
    parquet_abs = str(args.parquet.resolve())
    log.info(
        "loading parquet %s with %d partitions",
        parquet_abs,
        n_partitions,
    )
    ds = read_parquet_partitioned(parquet_abs, n_partitions=n_partitions)

    rss_start_gb = _peak_rss_gb()
    t0 = time.perf_counter()

    os.environ["GOLDENMATCH_DISTRIBUTED_PIPELINE"] = "2"
    os.environ["GOLDENMATCH_ENABLE_DISTRIBUTED_RAY"] = "1"

    block_shuffle = bool(int(args.block_shuffle))
    if block_shuffle:
        # HARD-set: these two flags define the recall-complete leg.
        os.environ["GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE"] = "1"
        os.environ["GOLDENMATCH_DISTRIBUTED_WCC"] = "randomized_contraction"
        # Force the WCC path on the few-M-row sim dataset (below the 50M-pair
        # threshold the route is scipy, which would vacuously validate scipy not
        # WCC). setdefault so an operator can override.
        os.environ.setdefault("GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD", "0")
        os.environ.setdefault(
            "GOLDENMATCH_DISTRIBUTED_WCC_SCRATCH",
            str(args.out.parent / "rc_scratch"),
        )

    # Write golden to a temp output dir so we can count multi-member clusters.
    # The pipeline does not materialise clusters on the driver (intentionally);
    # a parquet scan is the only way to get a multi_member_cluster_count without
    # introducing a take_all that would wedge the driver.
    #
    # PER-LEG dir derived from the --out stem: the baseline (--block-shuffle 0)
    # and recall-complete (--block-shuffle 1) legs use DIFFERENT --out files, so
    # this keeps their golden in separate dirs. Ray write_parquet appends
    # UUID-named part files without clearing, so a SHARED dir would let the
    # recall count include the baseline's golden and inflate the recall-vs-
    # baseline comparison (the whole point of the two-leg gate).
    golden_out = str(args.out.parent / f"{args.out.stem}_golden")

    # The pipeline auto-configures from the Ray Dataset itself; no
    # explicit config arg accepted. confidence_required=False keeps the
    # bench from raising ControllerNotConfidentError on the synthetic
    # fixture (the controller's gates target real-shape data).
    result = run_dedupe_pipeline_distributed(
        ds, confidence_required=False, output_path=golden_out,
    )

    wall_total = time.perf_counter() - t0
    rss_end_gb = _peak_rss_gb()

    # Count multi-member clusters from the golden parquet written by the
    # pipeline (one golden record per multi-member cluster). The pipeline
    # intentionally does NOT materialise clusters on the driver
    # (result.clusters == {}), so a parquet scan is the clean path.
    multi_member_cluster_count: int | None = None
    try:
        import os as _os  # noqa: PLC0415

        import polars as pl  # noqa: PLC0415

        golden_parts = [
            f for f in _os.listdir(golden_out)
            if f.endswith(".parquet")
        ] if _os.path.isdir(golden_out) else []
        if golden_parts:
            multi_member_cluster_count = (
                pl.scan_parquet(f"{golden_out}/**/*.parquet")
                .select(pl.len())
                .collect()
                .item()
            )
    except Exception as exc:
        log.warning("could not count multi-member clusters: %s", exc)

    summary = {
        "ray_address": ray_address,
        "cluster_resources": {
            k: float(v) for k, v in cluster_resources.items()
        },
        "parquet_path": str(args.parquet),
        "expected_rows": args.rows,
        "n_partitions": n_partitions,
        "identity_enabled": identity_enabled,
        "block_shuffle": block_shuffle,
        "wall_total_s": round(wall_total, 2),
        "driver_rss_start_gb": round(rss_start_gb, 3),
        "driver_rss_end_gb": round(rss_end_gb, 3),
        "driver_rss_peak_gb": round(rss_end_gb, 3),  # ru_maxrss is high-water
        "multi_member_cluster_count": multi_member_cluster_count,
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
