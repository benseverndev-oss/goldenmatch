"""Phase 5 kill criterion: 100M end-to-end on multi-node Ray cluster.

Requires:
    RAY_ADDRESS=ray://head:10001    # pre-provisioned multi-node Ray cluster
    GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1
    GOLDENMATCH_DISTRIBUTED_PIPELINE=2

Run:
    python scripts/bench_phase5_end2end.py \
        --input bench-dataset-v1/bench_100000000.parquet \
        --output bench-out/phase5_golden.parquet \
        --config phase5-synth        # explicit config; skips auto-config

Pass ``--config phase5-synth`` (or a config YAML path) to bypass auto-config:
at 100M, auto-config does ~40 full-dataset sample reads and can commit a
degenerate RED config that blocks on a low-cardinality field (-> billions of
pairs). ``--allow-red-config 1`` is the escape hatch if you must auto-config.

Kill criterion: total wall < 30 min.

This is the load-bearing Splink-Spark parity proof point. Single-node
runs at 100M would project to ~230 GB peak RSS (linear extrapolation
from 25M's 57.7 GB) — won't fit on the 64 GB bench runner. The
distributed pipeline is the only viable path at this scale.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import psutil

KILL_WALL_SEC = 30 * 60  # 30 minutes


def _build_config(spec: str | None):
    """Build an explicit GoldenMatchConfig from --config, or None to auto-config.

    ``phase5-synth`` is the built-in config for ``generate_phase5_dataset.py``
    output (columns ``__row_id__, first_name, last_name, email, zip``; the rows
    of one synthetic cluster share an identical ``last_name``). It blocks on
    ``last_name`` (unique per cluster -> blocks of exactly ``ROWS_PER_CLUSTER``,
    no block-size blowup) and scores with a WEIGHTED ``last_name`` matchkey
    using the ``exact`` scorer.

    Why weighted-with-exact-scorer rather than an ``exact`` matchkey: the
    block-shuffle explode (``_attach_colocation_keys``) emits one full-record
    copy per blocking pass AND per EXACT matchkey. Blocking + exact-matching the
    same field would explode each record twice (2x the ~27 GB shuffle that is
    the e2e wall, see the #844 PERF NOTE on ``_score_blocks_block_shuffle``). A
    weighted matchkey contributes no extra explode key, so this stays 1x while
    scoring identically (exact-agree on ``last_name`` -> 1.0 >= threshold). Any
    other --config value is a path to a config YAML.
    """
    if spec is None:
        return None
    if spec == "phase5-synth":
        from goldenmatch.config.schemas import (
            BlockingConfig,
            BlockingKeyConfig,
            GoldenMatchConfig,
            GoldenRulesConfig,
            MatchkeyConfig,
            MatchkeyField,
        )

        return GoldenMatchConfig(
            matchkeys=[
                MatchkeyConfig(
                    name="lastname_weighted",
                    type="weighted",
                    threshold=0.9,
                    fields=[
                        MatchkeyField(
                            field="last_name", scorer="exact", weight=1.0,
                        ),
                    ],
                ),
            ],
            blocking=BlockingConfig(
                strategy="static",
                keys=[BlockingKeyConfig(fields=["last_name"])],
            ),
            # golden survivorship: the pipeline builds GoldenRulesConfig() when
            # this is None, which fails validation (needs default_strategy).
            golden_rules=GoldenRulesConfig(default_strategy="first_non_null"),
        )
    from goldenmatch.config.loader import load_config

    return load_config(spec)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=str, required=True)
    ap.add_argument("--output", type=str, required=True)
    ap.add_argument("--kill-wall-sec", type=float, default=KILL_WALL_SEC)
    ap.add_argument(
        "--block-shuffle",
        choices=["0", "1"],
        default="0",
        help=(
            "1 = recall-complete leg: enables GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE "
            "and routes clustering to randomized_contraction WCC. "
            "Requires GOLDENMATCH_DISTRIBUTED_WCC_SCRATCH to be a gs:// path "
            "(node-local scratch silently breaks cross-node parquet reads)."
        ),
    )
    ap.add_argument(
        "--config",
        type=str,
        default=None,
        help=(
            "Explicit config (bypasses auto-config). Either 'phase5-synth' (a "
            "built-in config for generate_phase5_dataset.py output: block + exact "
            "match on last_name, which is unique per synthetic cluster -> small "
            "blocks, no block-size blowup) or a path to a config YAML. Omit to "
            "auto-configure. Auto-config is slow at 100M (~40 full-dataset sample "
            "reads) and can commit a degenerate RED config on synthetic data, so "
            "an explicit config is recommended for the benchmark."
        ),
    )
    ap.add_argument(
        "--allow-red-config",
        choices=["0", "1"],
        default="0",
        help=(
            "1 = pass allow_red_config=True (the post-#715 escape hatch): run a "
            "config the auto-config controller flagged RED instead of raising "
            "ControllerNotConfidentError. Ignored when --config is set."
        ),
    )
    args = ap.parse_args()

    block_shuffle = bool(int(args.block_shuffle))
    allow_red_config = bool(int(args.allow_red_config))

    os.environ.setdefault("GOLDENMATCH_ENABLE_DISTRIBUTED_RAY", "1")
    os.environ.setdefault("GOLDENMATCH_DISTRIBUTED_PIPELINE", "2")

    if block_shuffle:
        # HARD-set: these two flags define the recall-complete leg.
        os.environ["GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE"] = "1"
        os.environ["GOLDENMATCH_DISTRIBUTED_WCC"] = "randomized_contraction"
        # Force the distributed WCC path: below the 50M-pair threshold
        # build_clusters_distributed routes to the driver-collecting scipy
        # fallback REGARDLESS of the algorithm, which is the head-wedge the
        # recall-complete path exists to avoid. 100M block-shuffle pairs normally
        # exceed 50M, but pin it so the run can't silently land on scipy.
        # setdefault so an operator can still override.
        os.environ.setdefault("GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD", "0")
        # GCS scratch is REQUIRED on multi-node: a node-local path silently
        # breaks the cross-node parquet reads in the WCC per-round checkpoint.
        scratch = os.environ.get("GOLDENMATCH_DISTRIBUTED_WCC_SCRATCH", "")
        if not scratch or not scratch.startswith("gs://"):
            print(
                "ERROR: --block-shuffle 1 requires "
                "GOLDENMATCH_DISTRIBUTED_WCC_SCRATCH=gs://<bucket>/rc_scratch. "
                "A node-local scratch path silently breaks cross-node parquet reads "
                "in the WCC per-round checkpoint.",
                file=sys.stderr,
            )
            return 2

    from goldenmatch.distributed import read_partitioned
    from goldenmatch.distributed.pipeline import run_dedupe_pipeline_distributed

    cfg = _build_config(args.config)

    proc = psutil.Process()
    baseline = proc.memory_info().rss

    t_load = time.perf_counter()
    ds = read_partitioned(args.input, n_partitions=64)
    load_wall = time.perf_counter() - t_load

    t_pipe = time.perf_counter()
    result = run_dedupe_pipeline_distributed(
        ds,
        config=cfg,
        confidence_required=False,
        allow_red_config=allow_red_config,
        output_path=args.output,
    )
    pipe_wall = time.perf_counter() - t_pipe
    total = time.perf_counter() - t_load

    peak_gb = proc.memory_info().rss / 1024**3

    # Count multi-member clusters from the golden parquet written by the
    # pipeline (one golden record per multi-member cluster). result.clusters
    # is intentionally empty ({}) to avoid the driver-wedge at 100M.
    multi_member_cluster_count: int | None = None
    try:
        import polars as pl  # noqa: PLC0415

        # Scan the golden parquet directly -- args.output is the gs:// (or local)
        # path the pipeline wrote to, and polars/pyarrow read gs:// natively. Do
        # NOT pre-guard with os.path.isdir/listdir: those are local-FS calls that
        # return False on a gs:// path, which would leave the count None on
        # exactly the recall leg this instrumentation exists for. A missing/empty
        # path raises -> caught below -> count stays None.
        multi_member_cluster_count = (
            pl.scan_parquet(f"{args.output.rstrip('/')}/**/*.parquet")
            .select(pl.len())
            .collect()
            .item()
        )
    except Exception as exc:
        print(f"WARNING: could not count multi-member clusters: {exc}", file=sys.stderr)

    print(f"load_wall_sec={load_wall:.1f}")
    print(f"pipeline_wall_sec={pipe_wall:.1f}")
    print(f"total_wall_sec={total:.1f}")
    print(f"client_peak_rss_gb={peak_gb:.2f}")
    print(f"client_baseline_rss_gb={baseline / 1024**3:.2f}")
    print(f"clusters={len(result.clusters) if result else 0}")
    print(f"block_shuffle={block_shuffle}")
    print(f"config_mode={args.config or 'auto'}")
    print(f"allow_red_config={allow_red_config}")
    print(f"multi_member_cluster_count={multi_member_cluster_count}")

    if total >= args.kill_wall_sec:
        print(f"KILL: total wall {total:.1f}s >= {args.kill_wall_sec}s")
        return 1
    print(f"PASS: total wall {total:.1f}s under {args.kill_wall_sec}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
