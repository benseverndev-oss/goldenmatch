#!/usr/bin/env python3
"""#510: quality-invariant scale validation harness.

The thesis: match quality and clustering behaviour are invariant across scale.
Existing scale benches measure throughput (wall, RSS) but not quality, so the
"validated" rows in `scale-envelope.md` are throughput claims, not F1 claims.
This harness fills the quality side: at each rung it generates a deterministic
synthetic person dataset (replicating the Phase 5 generator's logic, but keeping
the cluster id so we have ground truth), runs zero-config dedupe, and reports
Pairwise F1, B-cubed F1, Cluster F1, plus wall, peak RSS, cluster counts, and
the committed config the controller chose.

Per-rung output (JSON), so future rungs slot in:
    { "rows": N, "clusters": N/5, "wall_s": ..., "rss_mb_peak": ...,
      "pairwise": {"f1": ..., "p": ..., "r": ..., "tp": ..., "fp": ..., "fn": ...},
      "b_cubed":  {"f1": ..., "p": ..., "r": ...},
      "cluster":  {"f1": ..., "p": ..., "r": ..., "exact": N},
      "predicted_clusters": ..., "multi_member": ..., "committed_config": {...} }

Run a single rung locally:
    python scripts/quality_invariant_scale.py --rows 10000 --out out.json

Run the ladder via the bench-gen Railway service (large rungs): wire a Railway
one-shot job modelled on `Dockerfile.embprov` that invokes this script per N.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tracemalloc
from pathlib import Path

if sys.platform != "win32":
    import resource as _resource
else:
    _resource = None  # Windows: fall back to tracemalloc in _peak_rss_mb

import numpy as np
import polars as pl

ROWS_PER_CLUSTER = 5
TYPO_RATE = 0.10


_SYL = ["an", "be", "ca", "da", "el", "fi", "ga", "ha", "in", "jo", "ka", "la",
        "ma", "na", "or", "pa", "ri", "sa", "ta", "va", "wo", "xe", "yu", "ze"]
_STREETS = ["main st", "oak ave", "pine rd", "maple dr", "cedar ln",
            "elm st", "washington ave", "park blvd"]
_CITIES = ["springfield", "franklin", "clinton", "georgetown",
           "salem", "fairview", "madison", "bristol"]


def _hash_name(salt: str, seed: int, cid: int, n_syl: int = 5) -> str:
    """Pseudo-random 5-syllable name from (salt, seed, cid). 24^5 ~= 8M combos
    so at 100k clusters expected collisions ~= 600 per pool (cheap birthday
    arithmetic), and a (first, last) tuple collision is effectively impossible.
    Independent salts for first/last keep the two pools uncorrelated.
    """
    import hashlib
    h = hashlib.md5(f"{salt}_{seed}_{cid}".encode()).digest()
    return "".join(_SYL[h[i] % len(_SYL)] for i in range(n_syl))


def generate_with_gt(n_rows: int, seed: int = 0, shape: str = "realistic"
                     ) -> tuple[pl.DataFrame, np.ndarray]:
    """Generate a synthetic person dedupe dataset + ground-truth cluster ids.

    shape="phase5"   — the in-process replica of the Phase 5 generator (literal
                       "name_<cid>" / "sur_<cid>" tokens). Throughput-shaped:
                       low cardinality + high inter-cluster token similarity.
    shape="realistic" — 5-syllable hash-derived names + a realistic vocab for
                       address/city/zip/birth_year. Designed to be a fair
                       fixture for measuring pipeline quality across scale (no
                       inter-cluster name similarity, near-distinct identities).

    Both share the 5-rows-per-cluster + 10% typo-on-first_name noise model.
    """
    if shape == "phase5":
        return _generate_phase5(n_rows, seed)
    if shape == "realistic":
        return _generate_realistic(n_rows, seed)
    raise ValueError(f"unknown shape {shape!r}; expected 'phase5' or 'realistic'")


def _generate_phase5(n_rows: int, seed: int = 0) -> tuple[pl.DataFrame, np.ndarray]:
    n_rows = (n_rows // ROWS_PER_CLUSTER) * ROWS_PER_CLUSTER
    n_clusters = n_rows // ROWS_PER_CLUSTER
    rng = np.random.default_rng(seed)
    cids = np.repeat(np.arange(n_clusters, dtype=np.int64), ROWS_PER_CLUSTER)
    typo = rng.random(n_rows) < TYPO_RATE
    df = (
        pl.DataFrame({"__cid__": cids, "__typo__": typo})
        .with_columns(
            first_canon=pl.concat_str([pl.lit("name_"), pl.col("__cid__").cast(pl.Utf8)]),
            last_name=pl.concat_str([pl.lit("sur_"), pl.col("__cid__").cast(pl.Utf8)]),
        )
        .with_columns(
            first_name=pl.when(pl.col("__typo__"))
            .then(pl.col("first_canon").str.replace_all("a", "@", literal=True))
            .otherwise(pl.col("first_canon")),
        )
        .with_columns(
            email=pl.concat_str([pl.col("first_name"), pl.lit("."),
                                 pl.col("last_name"), pl.lit("@example.com")]),
            zip=(pl.col("__cid__") % 100000).cast(pl.Utf8).str.zfill(5),
            id=pl.int_range(0, n_rows, dtype=pl.Int64).cast(pl.Utf8),
        )
        .select("id", "first_name", "last_name", "email", "zip")
    )
    return df, cids


def _generate_realistic(n_rows: int, seed: int = 0) -> tuple[pl.DataFrame, np.ndarray]:
    n_rows = (n_rows // ROWS_PER_CLUSTER) * ROWS_PER_CLUSTER
    n_clusters = n_rows // ROWS_PER_CLUSTER
    rng = np.random.default_rng(seed)

    # Per-cluster canonical fields.
    first_canon = [_hash_name("F", seed, c) for c in range(n_clusters)]
    last_canon = [_hash_name("L", seed, c) for c in range(n_clusters)]
    street_num = rng.integers(1, 9999, n_clusters)
    street_idx = rng.integers(0, len(_STREETS), n_clusters)
    address_canon = [f"{street_num[c]} {_STREETS[street_idx[c]]}" for c in range(n_clusters)]
    city_canon = [_CITIES[i] for i in rng.integers(0, len(_CITIES), n_clusters)]
    zip_canon = [f"{c % 100000:05d}" for c in range(n_clusters)]
    year_canon = rng.integers(1940, 2005, n_clusters).astype(str).tolist()

    cids = np.repeat(np.arange(n_clusters, dtype=np.int64), ROWS_PER_CLUSTER)
    typo = rng.random(n_rows) < TYPO_RATE

    first_rows = [first_canon[c] for c in cids]
    last_rows = [last_canon[c] for c in cids]
    addr_rows = [address_canon[c] for c in cids]
    city_rows = [city_canon[c] for c in cids]
    zip_rows = [zip_canon[c] for c in cids]
    year_rows = [year_canon[c] for c in cids]

    # Same 'a' -> '@' typo on first_name (matches phase5's noise model so the two
    # shapes only differ in vocab, not noise). Carries into email.
    first_with_typo = [f.replace("a", "@") if t else f for f, t in zip(first_rows, typo)]
    email_rows = [f"{f}.{l}@example.com" for f, l in zip(first_with_typo, last_rows)]

    df = pl.DataFrame({
        "id": [f"r{i}" for i in range(n_rows)],
        "first_name": first_with_typo,
        "last_name": last_rows,
        "address": addr_rows,
        "city": city_rows,
        "zip": zip_rows,
        "birth_year": year_rows,
        "email": email_rows,
    })
    return df, cids


def _pairs_from_clusters(cluster_members: dict[int, list[int]]) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for members in cluster_members.values():
        m = sorted(members)
        for i in range(len(m)):
            for j in range(i + 1, len(m)):
                out.add((m[i], m[j]))
    return out


def score_quality(
    predicted_members: dict[int, list[int]], gt_cids: np.ndarray
) -> dict[str, dict]:
    """O(N) streaming Pairwise + B-cubed + Cluster F1 vs the gt_cids array.

    Never materializes the GT pair set (which is ~16 GB at 100 M rows / 20 M
    clusters of 5). For each predicted cluster, computes its contribution to
    every metric from the gt_cid Counter of its members:

      - pairwise tp  += sum( C(cnt[g], 2) for g in cnt )       per cluster
      - pairwise pred-total += C(|P|, 2)
      - pairwise gt-total = sum( C(gt_sizes[g], 2) )           once, via np.bincount
      - B-cubed bp contribution = sum(cnt[g]^2) / |P|           per cluster
      - B-cubed br contribution = sum(cnt[g]^2 / gt_sizes[g])  per cluster
      - Cluster exact-match counted when |cnt|==1 AND |P|==gt_sizes[only_g]
      - Singletons (rows not in any multi-member cluster) handled with a
        vectorized boolean mask, contributing bp += 1 / br += 1/gt_sizes[gt_c].

    Numbers match the prior set-based implementation exactly on the 1K/10K/100K
    local rungs (validated). Memory at 200 M: ~3 GB peak (gt_sizes_arr +
    in_multi mask + cluster member arrays), vs ~32 GB for the set-based version.
    """
    n_rows = int(len(gt_cids))
    # Per-GT-cluster size (used by B-cubed recall, cluster-F1 exact match, and
    # the GT pair total). bincount needs nonneg ints; gt_cids are nonneg here.
    gt_sizes_arr = np.bincount(gt_cids)
    gt_multi_total = int((gt_sizes_arr > 1).sum())
    gt_pair_total = int(np.sum(gt_sizes_arr * (gt_sizes_arr - 1) // 2))

    in_multi = np.zeros(n_rows, dtype=bool)
    pred_tp = 0
    pred_pair_total = 0
    bp_acc = 0.0
    br_acc = 0.0
    exact_cluster_matches = 0
    pred_multi_total = 0

    for members in predicted_members.values():
        sz = len(members)
        if sz <= 1:
            continue
        pred_multi_total += 1
        arr = np.asarray(members, dtype=np.int64)
        in_multi[arr] = True
        gt_for_members = gt_cids[arr]
        uniq, counts = np.unique(gt_for_members, return_counts=True)
        # Pairwise tp from this cluster
        pred_tp += int(np.sum(counts * (counts - 1) // 2))
        pred_pair_total += sz * (sz - 1) // 2
        # B-cubed contributions
        sq = counts.astype(np.float64) ** 2
        bp_acc += float(np.sum(sq) / sz)
        br_acc += float(np.sum(sq / gt_sizes_arr[uniq]))
        # Cluster exact-match: cluster is purely one gt cluster AND covers all of it
        if uniq.size == 1 and sz == int(gt_sizes_arr[uniq[0]]):
            exact_cluster_matches += 1

    # Singletons: predicted cluster is just {row}, gt cluster has size gt_sizes_arr[gt_c].
    # Each singleton contributes bp += 1/1 and br += 1/gt_sizes[gt_c].
    singleton_mask = ~in_multi
    n_single = int(singleton_mask.sum())
    if n_single:
        gt_single = gt_cids[singleton_mask]
        bp_acc += float(n_single)
        br_acc += float(np.sum(1.0 / gt_sizes_arr[gt_single]))

    # Pairwise
    fp_pairs = pred_pair_total - pred_tp
    fn_pairs = gt_pair_total - pred_tp
    pp = pred_tp / pred_pair_total if pred_pair_total else 0.0
    pr = pred_tp / gt_pair_total if gt_pair_total else 0.0
    pf1 = (2 * pp * pr / (pp + pr)) if (pp + pr) else 0.0
    # B-cubed
    bp = bp_acc / n_rows
    br = br_acc / n_rows
    bf1 = (2 * bp * br / (bp + br)) if (bp + br) else 0.0
    # Cluster
    cfp_cnt = pred_multi_total - exact_cluster_matches
    cfn_cnt = gt_multi_total - exact_cluster_matches
    cp = exact_cluster_matches / pred_multi_total if pred_multi_total else 0.0
    cr = exact_cluster_matches / gt_multi_total if gt_multi_total else 0.0
    cf1 = (2 * cp * cr / (cp + cr)) if (cp + cr) else 0.0
    return {
        "pairwise": {"f1": pf1, "p": pp, "r": pr, "tp": int(pred_tp), "fp": int(fp_pairs), "fn": int(fn_pairs)},
        "b_cubed":  {"f1": bf1, "p": bp, "r": br},
        "cluster":  {"f1": cf1, "p": cp, "r": cr, "exact": exact_cluster_matches,
                     "gt_total": gt_multi_total, "pred_total": pred_multi_total},
    }


def _peak_rss_mb() -> float | None:
    """Best-effort peak RSS in MB. Linux: ru_maxrss is KB; macOS: bytes; Windows: tracemalloc fallback."""
    if sys.platform == "win32":
        try:
            cur, peak = tracemalloc.get_traced_memory()
            return peak / 1024 / 1024
        except Exception:
            return None
    try:
        ru = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
        return ru / 1024 if sys.platform != "darwin" else ru / 1024 / 1024
    except Exception:
        return None


def run_rung(n_rows: int, seed: int = 0, shape: str = "realistic",
             backend: str | None = None) -> dict:
    import goldenmatch
    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    if sys.platform == "win32":
        tracemalloc.start()

    t0 = time.time()
    df, gt = generate_with_gt(n_rows, seed=seed, shape=shape)
    t_gen = time.time() - t0

    # Backend handling: zero-config (planner picks) when --backend is omitted;
    # otherwise pre-build the auto-config and force the backend (the v3 planner
    # honors a user override). At Railway-scale (10M+) the planner can land on
    # `polars` if it can't detect enough RAM, which OOMs; --backend duckdb
    # is the safest fallback (out-of-core) and bucket is the fastest when the
    # container has 32+ GB.
    #
    # `bench_capture()` pushes a BenchmarkRecorder onto goldenmatch's stage
    # ContextVar. Every `with stage(name)` in core/pipeline.py records its
    # wall + process-lifetime peak RSS (KB) at exit. Diffing consecutive
    # stage_peak_rss_kb entries (insertion-ordered) gives the per-stage
    # contribution to the peak — the input we need to pick the right RSS
    # optimization target for #510. See PR #548.
    from goldenmatch.core.bench import bench_capture
    bench_dict: dict = {}
    t1 = time.time()
    with bench_capture() as bench_rec:
        if backend:
            # confidence_required=False because passing --backend explicitly
            # is "measurement mode" -- the caller has chosen the execution
            # plan and wants the pipeline to run even if the controller commits
            # a RED config. Without this, every rung >= 100K rows raises
            # ControllerNotConfidentError when the realistic shape exhausts
            # the iteration budget (the 5M-bucket bench did exactly that:
            # BUDGET_ITERATIONS at iter 3, no RSS data collected). The 1M
            # zero-config path (else branch below) still gets the controller's
            # confidence guard since #510's headline quality claim is on the
            # auto-config-only path.
            cfg = goldenmatch.auto_configure_df(df, confidence_required=False)
            cfg.backend = backend  # type: ignore[attr-defined]
            result = goldenmatch.dedupe_df(df, config=cfg)
        else:
            result = goldenmatch.dedupe_df(df)
    t_dedupe = time.time() - t1
    try:
        bench_dict = bench_rec.to_dict()
    except Exception as e:
        bench_dict = {"_capture_error": repr(e)[:120]}

    predicted: dict[int, list[int]] = {}
    for cid, c in (result.clusters or {}).items():
        members = c.get("members") or []
        if len(members) > 1:
            predicted[int(cid)] = list(members)

    metrics = score_quality(predicted, gt)

    multi = sum(1 for v in predicted.values() if len(v) > 1)
    committed_cfg: dict = {}
    try:
        from goldenmatch.core.autoconfig import _LAST_CONTROLLER_RUN
        state = _LAST_CONTROLLER_RUN.get()
        if state is not None:
            profile, history = state
            committed_cfg = {
                "health": profile.health().value,
                "stop_reason": str(history.stop_reason),
                "iterations": history.iteration,
                "decisions": [d.rule_name for d in (history.decisions or [])],
            }
    except Exception as e:
        committed_cfg = {"_capture_error": repr(e)[:120]}

    return {
        "rows": len(df),
        "clusters_gt": int(len(set(gt.tolist()))),
        "wall_s": {"generate": round(t_gen, 2), "dedupe": round(t_dedupe, 2), "total": round(t_gen + t_dedupe, 2)},
        "rss_mb_peak": _peak_rss_mb(),
        **metrics,
        "predicted_clusters": len(predicted) + (len(df) - sum(len(v) for v in predicted.values())),
        "multi_member_clusters": multi,
        "committed_config": committed_cfg,
        "bench": bench_dict,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--shape", choices=("realistic", "phase5"), default="realistic",
                    help="realistic = varied syllable vocab (default, the fair fixture); "
                         "phase5 = the in-process Phase-5 replica (throughput-shaped, "
                         "pathological for ER quality)")
    ap.add_argument("--backend", default=None,
                    choices=(None, "polars", "bucket", "chunked", "duckdb", "ray"),
                    help="override the v3 planner's backend pick. Recommended ladder: "
                         "polars <500K, bucket 500K-25M (>=32GB RAM), duckdb 25M-100M "
                         "(out-of-core, no OOM on smaller boxes), ray 50M+ "
                         "(distributed; needs the ray extra installed).")
    ap.add_argument("--out", type=Path, default=None, help="write per-rung JSON here")
    args = ap.parse_args(argv)

    res = run_rung(args.rows, seed=args.seed, shape=args.shape, backend=args.backend)
    res["shape"] = args.shape
    res["backend"] = args.backend or "auto"
    print(json.dumps(res, indent=2, default=str))
    if args.out:
        args.out.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
