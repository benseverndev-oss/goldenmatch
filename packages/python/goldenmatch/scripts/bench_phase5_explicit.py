"""Phase 5 100M with an EXPLICIT config (bypasses auto-config).

Isolates the 100M driver-wedge cause: the standard bench_phase5_end2end.py
calls auto_configure_df inside the distributed pipeline, which at 100M
time-budget-failed into a RED config. This driver replicates the SAME Phase-5
distributed stages but with a hand-built config (perfect for the synthetic
generator: last_name == "sur_<cid>" is unique per cluster), so:
  - if this COMPLETES -> auto-config's RED config was the wedge cause.
  - if this STILL wedges the head -> the driver-side materialize_cluster_dict /
    member_to_cid take-alls are the irreducible ceiling (config-independent).

Env: RAY_ADDRESS=auto GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1 (no PIPELINE flag
needed -- this calls the stages directly).
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import psutil


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    os.environ.setdefault("GOLDENMATCH_ENABLE_DISTRIBUTED_RAY", "1")

    # Surface distributed_wcc's per-round convergence logs in stdout so the
    # GCP monitor can watch WCC progress (round N: changed=K).
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("goldenmatch.distributed.clustering").setLevel(logging.INFO)

    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        GoldenRulesConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    from goldenmatch.distributed import read_partitioned
    from goldenmatch.distributed.clustering import local_cc_assignments
    from goldenmatch.distributed.golden import build_golden_records_distributed
    from goldenmatch.distributed.pipeline import _join_assignments_distributed
    from goldenmatch.distributed.scoring import score_blocks_distributed

    # EXPLICIT config -- perfect for the generator's shape. last_name == cluster
    # id, so blocking on it gives exactly one cluster per block; score the only
    # varying field (first_name, jaro_winkler) at 0.85. No auto-config.
    cfg = GoldenMatchConfig(
        blocking=BlockingConfig(
            strategy="static", keys=[BlockingKeyConfig(fields=["last_name"])]
        ),
        matchkeys=[
            MatchkeyConfig(
                name="first_name_fuzzy",
                type="weighted",
                fields=[
                    MatchkeyField(field="first_name", scorer="jaro_winkler", weight=1.0)
                ],
                threshold=0.85,
            )
        ],
        golden_rules=GoldenRulesConfig(default_strategy="most_complete"),
    )

    proc = psutil.Process()
    baseline = proc.memory_info().rss / 1024**3

    t0 = time.perf_counter()
    ds = read_partitioned(args.input, n_partitions=64)
    load_wall = time.perf_counter() - t0

    t1 = time.perf_counter()
    # Fully distributed: assignments stay a Ray Dataset (no materialize_cluster_dict),
    # rows annotated via distributed hash join (no member_to_cid driver dict),
    # golden via distributed groupby.
    raw_pairs_ds = score_blocks_distributed(ds, cfg)
    assignments_ds = local_cc_assignments(raw_pairs_ds)
    multi_ds = _join_assignments_distributed(ds, assignments_ds)
    user_columns = [c for c in ds.schema().names if not c.startswith("__")]
    # materialize once (in the distributed object store, NOT the driver) so the
    # write + count don't each re-run the whole score->cc->join->golden lineage.
    golden_ds = build_golden_records_distributed(
        multi_ds, cfg.golden_rules, user_columns=user_columns,
    ).materialize()
    # Distributed write -- never collect golden to the driver.
    golden_ds.write_parquet(args.output)
    n_golden = golden_ds.count()  # distributed count (int), no driver collect
    pipe_wall = time.perf_counter() - t1
    total = time.perf_counter() - t0

    peak_gb = proc.memory_info().rss / 1024**3
    print(f"load_wall_sec={load_wall:.1f}")
    print(f"pipeline_wall_sec={pipe_wall:.1f}")
    print(f"total_wall_sec={total:.1f}")
    print(f"client_baseline_rss_gb={baseline:.2f}")
    print(f"client_peak_rss_gb={peak_gb:.2f}")
    print(f"golden_records={n_golden}")
    print("EXPLICIT_CONFIG_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
