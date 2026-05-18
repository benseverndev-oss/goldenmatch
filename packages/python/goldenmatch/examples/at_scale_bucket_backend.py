"""5M-on-one-node dedupe with the bucket backend (v1.16.0).

The bucket backend is the recommended path for 5M+ row dedupe workloads
on a single high-core / mid-memory box (e.g. 16-core / 64 GB). It
replaces the per-block LazyFrame pattern (which OOM'd on Linux runners
at 5M scale) with a hash-bucketed eager partition + fast in-process
scorer.

Measured on a 16-core / 64 GB GitHub runner at 5M rows:
- 9.94 min wall
- 6.4 GB peak RSS
- 1.67M multi-member clusters

Topics covered (each section is independent):
1. Headline: zero-config bucket backend at 5M
2. _skip_finalize=True for benchmarking your own configs
3. Distributed Plan v1 (ray) opt-in via env var
4. Bench harness --run-treatment flag for kill-criterion audits
"""
from __future__ import annotations

import os
import time

import polars as pl

import goldenmatch as gm
from goldenmatch.core.autoconfig import auto_configure_df


# ---------------------------------------------------------------------------
# 1. Headline: 5M-on-one-node bucket backend
# ---------------------------------------------------------------------------

def headline_5m_bucket_backend(df: pl.DataFrame) -> gm.DedupeResult:
    """Recommended config for 5M+ dedupe on a single 16-core / 64 GB node.

    The auto-config v3 planner picks ``backend="bucket"`` automatically
    when the workload fits the "fast box" pattern (32+ GB RAM, no user
    backend override). Override the backend explicitly if you want to
    pin to bucket regardless of planner output.
    """
    cfg = auto_configure_df(df, confidence_required=False)
    cfg.backend = "bucket"

    t = time.perf_counter()
    result = gm.dedupe_df(df, config=cfg, confidence_required=False)
    print(
        f"5M dedupe: {time.perf_counter() - t:.1f}s wall, "
        f"{len(result.clusters)} clusters"
    )
    return result


# ---------------------------------------------------------------------------
# 2. Skip controller finalize for your own benchmarks
# ---------------------------------------------------------------------------

def own_bench_skip_finalize(df: pl.DataFrame) -> gm.DedupeResult:
    """Use ``_skip_finalize=True`` when measuring your own pipeline.

    By default, ``auto_configure_df`` runs the committed config through
    the full pipeline once at the end of controller iteration so that
    callers get a warm cache and PostflightReport data. For benchmarks
    you usually want to time the pipeline run yourself, separately from
    auto-config -- otherwise the full-pipeline call inside
    ``auto_configure_df`` doubles wall and confuses stage timings.

    ``_skip_finalize=True`` is a stable, supported knob despite the
    underscore prefix. See PR #316 for the rationale.
    """
    t0 = time.perf_counter()
    cfg = auto_configure_df(df, confidence_required=False, _skip_finalize=True)
    t_cfg = time.perf_counter() - t0
    cfg.backend = "bucket"

    t1 = time.perf_counter()
    result = gm.dedupe_df(df, config=cfg, confidence_required=False)
    t_pipe = time.perf_counter() - t1

    print(f"auto_config: {t_cfg:.1f}s  pipeline: {t_pipe:.1f}s")
    return result


# ---------------------------------------------------------------------------
# 3. Distributed Plan v1 (ray) opt-in
# ---------------------------------------------------------------------------

def distributed_plan_v1_opt_in(df: pl.DataFrame) -> gm.DedupeResult:
    """Opt back into the Distributed Plan v1 stack.

    Distributed Plan v1 (``backend="ray"`` + ``prepared_record_store=True``
    + ``partitioned_block_scoring=True``) was soft-reverted in v1.16 after
    failing the 5M kill criterion on the same workload where the bucket
    backend completed at 6.4 GB peak RSS. The v3 planner no longer
    auto-picks ray.

    Two ways to opt back in:
      1. Env var (lets the v3 planner consider ray when row count
         >= 50M and ray is installed):
           os.environ["GOLDENMATCH_ENABLE_DISTRIBUTED_RAY"] = "1"
      2. Explicit backend selection (always works):
           cfg.backend = "ray"

    NOTE: ``pip install goldenmatch[ray]`` is required.
    """
    os.environ["GOLDENMATCH_ENABLE_DISTRIBUTED_RAY"] = "1"
    cfg = auto_configure_df(df, confidence_required=False)
    cfg.backend = "ray"
    cfg.prepared_record_store = True
    cfg.partitioned_block_scoring = True
    return gm.dedupe_df(df, config=cfg, confidence_required=False)


# ---------------------------------------------------------------------------
# 4. Bench harness --run-treatment flag (post-soft-revert kill-criterion audit)
# ---------------------------------------------------------------------------

# scripts/bench_distributed_stack.py compares the bucket baseline against
# the Distributed Plan v1 treatment. Treatment is OFF by default since
# v1.16.0 because it consistently fails the 20% wall + 20% RSS kill
# criterion on the 5M fixture (see PR #318 for the original verdict).
#
# To rerun the kill-criterion audit (e.g. before reviving Distributed
# Plan v2 work), pass --run-treatment:
#
#   python packages/python/goldenmatch/scripts/bench_distributed_stack.py \
#     --dataset bench-dataset/bench_5000000.parquet \
#     --run-treatment
#
# Without the flag, only the baseline runs and ``kill_criterion.verdict``
# is "SKIPPED".


if __name__ == "__main__":
    # Minimal demo dataset (the real bench fixture lives in
    # scripts/generate_bench_dataset.py).
    df = pl.DataFrame({
        "first_name": ["Alice", "alice", "Alicia", "Bob", "bob", "Bobby", "Carol", "carol", "Caroline"],
        "last_name":  ["Smith", "smith", "Smyth", "Jones", "jones", "Jonez", "Brown", "brown", "Browne"],
        "email":      ["a@x.com"] * 3 + ["b@x.com"] * 3 + ["c@x.com"] * 3,
        "zip":        ["10001"] * 9,
    })
    headline_5m_bucket_backend(df)
