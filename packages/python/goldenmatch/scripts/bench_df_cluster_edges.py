"""Task 5 bench: 3-way cluster-edge view at scale (legacy dict vs embedded
DataFusion stream vs Polars-lazy), on a HEAVY-TAILED cluster-size distribution.

Measures wall + peak RSS for building the per-cluster edge view three ways, each
variant in its OWN subprocess (clean per-variant peak RSS), catching a child OOM
(non-zero / -9 / 137 exit) as an infeasibility data point rather than crashing:

  * ``legacy``    -- ``ClusterPairScores.from_frames(assignments, pairs)`` then
    drain ``.for_cluster(cid)`` per cid (the dict-of-dicts path; the 566s leg).
    This is LAST-WINS on duplicate ``(a, b)`` pairs.
  * ``datafusion`` -- ``cluster_edges_datafusion(...)`` consumed STREAMING: iterate
    the cid-ORDERED RecordBatch stream, group CONTIGUOUS same-cid runs, count edges
    per run, and DISCARD each run before the next. We never call ``_collect_runs``
    / never build the global ``{cid: {(a, b): score}}`` dict -- that is the whole
    point of the RSS measurement. The rollup table is also materialized.
  * ``polars``    -- the same join/filter via Polars lazy + streaming collect
    (dedup MAX by (a, b) -> join assignments twice -> same-cid filter -> sort cid).
    Attribution control: isolates "DataFusion-specific" from "just not a Python
    dict".

DEDUP NOTE (parity-clean divergence number). The bench input contains ~1%
DUPLICATE canonical ``(a, b)`` pairs with different scores. DataFusion + Polars
dedup by MAX(score); ``from_frames`` is LAST-WINS. To keep the headline number
(bottleneck-divergence rate) attributable to the TIE-BREAK RULE alone and not to
input multiplicity, we PRE-DEDUP the legacy input to one row per ``(a, b)`` by
MAX before the legacy leg too. The MAX-vs-LAST divergence is then measured
SEPARATELY (see ``_divergence_rate``) on the RAW (pre-dedup) input so the
duplicate-tie-break blast radius is still reported.

Local smoke: ``python bench_df_cluster_edges.py --np 50000 --runs 1`` (resource is
unavailable on Windows so RSS is 0.0 -- fine). DO NOT run the real bench locally
(box hangs on import). The bench runs in CI (Linux).
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


# ---------------------------------------------------------------------------
# Heavy-tailed generator (numpy-vectorized; scales to 200M pairs)
# ---------------------------------------------------------------------------
def make_heavytailed(n_pairs: int, seed: int = 0):
    """Build a heavy-tailed cluster partition + a SPARSE, partially-connected pair
    set with varied scores and ~1% duplicate canonical pairs.

    Returns ``(pairs_arrow, assignments_arrow)``:
      * pairs       : pyarrow table {a:i64, b:i64, score:f64}, RAW (AS-GIVEN, with
                      ~1% duplicate (a, b) at a DIFFERENT score so MAX-dedup and
                      the legacy LAST-WINS tie-break both get exercised).
      * assignments : pyarrow table {member_id:i64, cluster_id:i64}, ONE row per
                      member, member_id unique.

    Cluster-size distribution (the heavy tail):
      - MOST clusters are size 2-5 (the body),
      - a long tail of mid-size clusters (a Pareto/Zipf-ish draw, clipped),
      - a FEW oversized clusters (>100), drawn explicitly so the tail is present
        even at small n.
    Connectivity is PARTIAL: within a cluster we emit roughly ``c * size`` random
    within-cluster edges (c ~ 1.5), NOT the full ``size*(size-1)/2`` clique, so the
    rollup's connectivity ratio is a real fraction < 1 and the edge set is sparse.

    Vectorized with numpy throughout (member-id assignment, edge endpoint draws,
    score draws, duplicate injection) so it scales to 200M pairs -- mirrors
    ``bench_pipeline_complete_path.py:_make_pairs_df`` in spirit (no per-pair
    Python loop on the hot path).
    """
    import numpy as np
    import pyarrow as pa

    rng = np.random.default_rng(seed)
    target = max(1, int(n_pairs))

    # --- 1. Draw cluster sizes until we have enough EDGE CAPACITY ---------
    # Average edges/cluster ~ c * mean_size. We over-allocate sizes in blocks and
    # truncate once cumulative edge capacity >= target.
    c = 1.5  # edges per member (sparse: well below the (size-1)/2 clique density)

    sizes_blocks: list[Any] = []
    cap = 0
    # Tail knobs: ~0.3% of clusters are oversized (>100).
    while cap < target:
        block = 1_000_000
        # Body: size 2-5, heavy weight. Tail: Zipf-ish 6..~80. Oversized: 101..400.
        u = rng.random(block)
        body = rng.integers(2, 6, size=block)                      # 2..5
        # Zipf-like mid tail via (1/u)**k clipped; gives a long thin tail.
        midtail = np.clip((2.0 / np.clip(u, 1e-6, 1.0)) ** 1.3, 6, 80).astype(
            np.int64
        )
        oversized = rng.integers(101, 401, size=block)
        sizes = np.where(
            u < 0.90, body, np.where(u < 0.997, midtail, oversized)
        ).astype(np.int64)
        sizes_blocks.append(sizes)
        # edge capacity contributed (sparse model, but never exceed clique cap)
        clique = sizes * (sizes - 1) // 2
        sparse = np.maximum(1, np.ceil(c * sizes).astype(np.int64))
        cap += int(np.minimum(clique, sparse).sum())

    sizes = np.concatenate(sizes_blocks)
    clique = sizes * (sizes - 1) // 2
    edges_per = np.minimum(clique, np.maximum(1, np.ceil(c * sizes).astype(np.int64)))
    # Truncate clusters so cumulative edges just covers `target`.
    cum = np.cumsum(edges_per)
    keep = int(np.searchsorted(cum, target, side="left")) + 1
    keep = min(keep, sizes.shape[0])
    sizes = sizes[:keep]
    edges_per = edges_per[:keep]
    n_clusters = sizes.shape[0]

    # --- 2. Assign member ids (contiguous blocks per cluster) ------------
    starts = np.zeros(n_clusters, dtype=np.int64)
    np.cumsum(sizes[:-1], out=starts[1:])
    n_members = int(starts[-1] + sizes[-1]) if n_clusters else 0
    member_id = np.arange(n_members, dtype=np.int64)
    # cluster_id per member: repeat cluster index by its size.
    cluster_id = np.repeat(np.arange(n_clusters, dtype=np.int64), sizes)

    # --- 3. Draw within-cluster edge endpoints (vectorized) --------------
    total_edges = int(edges_per.sum())
    # Per-edge owning cluster index (repeat cluster idx by edges_per).
    edge_cluster = np.repeat(np.arange(n_clusters, dtype=np.int64), edges_per)
    base = starts[edge_cluster]          # member-id base of the owning cluster
    csize = sizes[edge_cluster]          # owning cluster size
    # Two distinct local offsets in [0, size) per edge; resample collisions once.
    off_a = (rng.random(total_edges) * csize).astype(np.int64)
    off_b = (rng.random(total_edges) * csize).astype(np.int64)
    coll = off_a == off_b
    if coll.any():
        off_b[coll] = (off_b[coll] + 1) % csize[coll]
    a = base + off_a
    b = base + off_b
    # Canonicalize endpoints (a < b) so duplicate detection is on canonical pairs.
    lo = np.minimum(a, b)
    hi = np.maximum(a, b)
    a, b = lo, hi
    score = rng.random(total_edges) * 0.999 + 0.001   # (0, 1], varied

    # --- 4. Inject ~1% DUPLICATE canonical pairs at a DIFFERENT score ----
    n_dup = max(1, total_edges // 100)
    dup_idx = rng.integers(0, total_edges, size=n_dup)
    dup_a = a[dup_idx]
    dup_b = b[dup_idx]
    # Different score: offset and re-clip into (0, 1].
    dup_score = np.clip(score[dup_idx] * 0.5 + 0.25 + rng.random(n_dup) * 0.2,
                        0.001, 1.0)
    a = np.concatenate([a, dup_a])
    b = np.concatenate([b, dup_b])
    score = np.concatenate([score, dup_score])

    pairs = pa.table(
        {
            "a": pa.array(a, pa.int64()),
            "b": pa.array(b, pa.int64()),
            "score": pa.array(score, pa.float64()),
        }
    )
    assignments = pa.table(
        {
            "member_id": pa.array(member_id, pa.int64()),
            "cluster_id": pa.array(cluster_id, pa.int64()),
        }
    )
    return pairs, assignments


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _peak_rss_mb() -> float:
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:
        return 0.0


def _assignments_to_polars(assignments_arrow):
    import polars as pl
    return pl.from_arrow(assignments_arrow)


def _pairs_max_dedup_list(pairs_arrow) -> list[tuple[int, int, float]]:
    """Pre-dedup the RAW pairs to ONE row per (a, b) by MAX(score), returned as a
    list[(a, b, score)]. Used by the legacy leg so the bottleneck-divergence number
    is attributable to the TIE-BREAK rule alone, not to input multiplicity (see the
    module docstring). Vectorized via Polars group_by max."""
    import polars as pl

    df = pl.from_arrow(pairs_arrow)
    g = (
        df.group_by("a", "b")
        .agg(pl.col("score").max().alias("score"))
    )
    return list(
        zip(
            g["a"].to_list(),
            g["b"].to_list(),
            g["score"].to_list(),
        )
    )


# ---------------------------------------------------------------------------
# Variant: legacy (ClusterPairScores dict path)
# ---------------------------------------------------------------------------
def _run_legacy(pairs_arrow, assignments_arrow) -> dict[str, Any]:
    from goldenmatch.core.cluster_pairscores import ClusterPairScores

    assignments_pl = _assignments_to_polars(assignments_arrow)
    # PRE-DEDUP to one row per (a, b) by MAX so the divergence number is tie-break
    # attributable (matches the DataFusion/Polars legs' MAX dedup on input).
    pairs_list = _pairs_max_dedup_list(pairs_arrow)
    cids = assignments_pl["cluster_id"].unique().to_list()

    t0 = time.perf_counter()
    view = ClusterPairScores.from_frames(assignments_pl, pairs_list)
    # Drain every cluster's edges (touch the items, emit nothing -- the identity
    # consumer's access pattern). This is the dict-rebuild-per-cid cost.
    total_edges = 0
    for cid in cids:
        edges = view.for_cluster(int(cid))
        total_edges += len(edges)
        del edges
    wall = time.perf_counter() - t0
    return {"wall_s": wall, "edges_seen": total_edges, "dedup": "pre-max"}


# ---------------------------------------------------------------------------
# Variant: datafusion (STREAMING -- never builds the full dict)
# ---------------------------------------------------------------------------
def _run_datafusion(pairs_arrow, assignments_arrow, memory_limit) -> dict[str, Any]:
    from goldenmatch.core.cluster_edges_df import cluster_edges_datafusion

    t0 = time.perf_counter()
    edges_stream, rollup_table = cluster_edges_datafusion(
        pairs_arrow, assignments_arrow, memory_limit=memory_limit,
    )

    # STREAMING consumption: the stream is cid-ORDERED, so same-cid rows are
    # contiguous ACROSS batches. We accumulate only the CURRENT run's edge count
    # (a single int + the current cid), emit/discard it when the cid changes, and
    # never retain prior runs. NO _collect_runs, NO global {cid: {(a,b): score}}.
    n_clusters_with_edges = 0
    total_edges = 0
    cur_cid: int | None = None
    cur_count = 0
    for raw_batch in edges_stream:
        batch = raw_batch.to_pyarrow() if hasattr(raw_batch, "to_pyarrow") else raw_batch
        cids = batch.column("cid").to_pylist()
        # Minimal per-row work: walk the contiguous cid runs in this batch.
        for cid in cids:
            cid = int(cid)
            if cur_cid is None:
                cur_cid = cid
                cur_count = 1
            elif cid == cur_cid:
                cur_count += 1
            else:
                # run for cur_cid finished -> account + DISCARD before next run
                n_clusters_with_edges += 1
                total_edges += cur_count
                cur_cid = cid
                cur_count = 1
    if cur_cid is not None:
        n_clusters_with_edges += 1
        total_edges += cur_count

    rollup_rows = rollup_table.num_rows
    wall = time.perf_counter() - t0
    return {
        "wall_s": wall,
        "edges_seen": total_edges,
        "clusters_with_edges": n_clusters_with_edges,
        "rollup_rows": rollup_rows,
    }


# ---------------------------------------------------------------------------
# Variant: polars (lazy + streaming collect) -- attribution control
# ---------------------------------------------------------------------------
def _run_polars(pairs_arrow, assignments_arrow) -> dict[str, Any]:
    import polars as pl

    pairs = pl.from_arrow(pairs_arrow)
    assignments = pl.from_arrow(assignments_arrow)

    t0 = time.perf_counter()
    amap_a = assignments.lazy().select(
        pl.col("member_id").alias("a"), pl.col("cluster_id").alias("cid_a")
    )
    amap_b = assignments.lazy().select(
        pl.col("member_id").alias("b"), pl.col("cluster_id").alias("cid_b")
    )
    deduped = (
        pairs.lazy()
        .group_by("a", "b")
        .agg(pl.col("score").max().alias("score"))
    )
    edges = (
        deduped.join(amap_a, on="a", how="inner")
        .join(amap_b, on="b", how="inner")
        .filter(pl.col("cid_a") == pl.col("cid_b"))
        .select(pl.col("cid_a").alias("cid"), "a", "b", "score")
        .sort("cid")
    )
    edge_count = edges.select(pl.len()).collect(streaming=True).item()

    # Rollup: per-cid aggregate (min / avg / count). Streaming collect.
    rollup = (
        edges.group_by("cid")
        .agg(
            pl.col("score").min().alias("min_edge"),
            pl.col("score").mean().alias("avg_edge"),
            pl.len().alias("edge_count"),
        )
    )
    rollup_rows = rollup.select(pl.len()).collect(streaming=True).item()
    wall = time.perf_counter() - t0
    return {"wall_s": wall, "edges_seen": int(edge_count),
            "rollup_rows": int(rollup_rows)}


# ---------------------------------------------------------------------------
# Bottleneck-divergence rate (datafusion MAX vs legacy LAST-WINS)
# ---------------------------------------------------------------------------
def _divergence_rate(pairs_arrow, assignments_arrow) -> dict[str, Any]:
    """Fraction of clusters whose BOTTLENECK edge differs between the DataFusion
    rule (MAX dedup, lexicographic (a, b) tie-break) and the legacy rule (LAST-WINS
    dedup) on the RAW pairs. Both legs see the SAME membership; the only difference
    is the duplicate-pair tie-break, so this isolates the parity-relaxation blast
    radius (#694's R1/R2/R3 framing).

    Computed in-process with Polars (cheap, no per-cluster Python dict). Bottleneck
    = the edge with the smallest score; ties broken lexicographically by (a, b)
    ascending -- the same rule the DataFusion rollup uses."""
    import polars as pl

    pairs = pl.from_arrow(pairs_arrow).with_row_index("__i__")
    assignments = pl.from_arrow(assignments_arrow)
    amap_a = assignments.select(
        pl.col("member_id").alias("a"), pl.col("cluster_id").alias("cid_a")
    )
    amap_b = assignments.select(
        pl.col("member_id").alias("b"), pl.col("cluster_id").alias("cid_b")
    )
    joined = (
        pairs.join(amap_a, on="a", how="inner")
        .join(amap_b, on="b", how="inner")
        .filter(pl.col("cid_a") == pl.col("cid_b"))
        .select(pl.col("cid_a").alias("cid"), "a", "b", "score", "__i__")
    )

    # DataFusion rule: per (cid, a, b) take MAX score, then per cid the min-score
    # edge with lexicographic (a, b) tie-break.
    df_dedup = joined.group_by("cid", "a", "b").agg(
        pl.col("score").max().alias("score")
    )
    df_bn = (
        df_dedup.sort("score", "a", "b")
        .group_by("cid")
        .agg(pl.col("a").first().alias("a"), pl.col("b").first().alias("b"),
             pl.col("score").first().alias("score"))
    )

    # Legacy rule: per (cid, a, b) take the LAST occurrence's score (by input idx),
    # then the same lexicographic min-score bottleneck.
    lg_dedup = (
        joined.sort("__i__")
        .group_by("cid", "a", "b")
        .agg(pl.col("score").last().alias("score"))
    )
    lg_bn = (
        lg_dedup.sort("score", "a", "b")
        .group_by("cid")
        .agg(pl.col("a").first().alias("a"), pl.col("b").first().alias("b"),
             pl.col("score").first().alias("score"))
    )

    cmp = df_bn.join(lg_bn, on="cid", how="inner", suffix="_lg")
    n_clusters = cmp.height
    if n_clusters == 0:
        return {"divergence_rate": 0.0, "n_clusters": 0, "n_divergent": 0}
    divergent = cmp.filter(
        (pl.col("a") != pl.col("a_lg"))
        | (pl.col("b") != pl.col("b_lg"))
        | (pl.col("score") != pl.col("score_lg"))
    ).height
    return {
        "divergence_rate": divergent / n_clusters,
        "n_clusters": n_clusters,
        "n_divergent": divergent,
    }


# ---------------------------------------------------------------------------
# Child driver (one variant in its own process for isolated peak RSS)
# ---------------------------------------------------------------------------
def _run_child(variant: str, n_pairs: int, seed: int, runs: int,
               memory_limit) -> int:
    pairs_arrow, assignments_arrow = make_heavytailed(n_pairs, seed=seed)
    actual_pairs = pairs_arrow.num_rows
    n_members = assignments_arrow.num_rows
    n_clusters = (
        _assignments_to_polars(assignments_arrow)["cluster_id"].n_unique()
    )

    def _one() -> dict[str, Any]:
        if variant == "legacy":
            return _run_legacy(pairs_arrow, assignments_arrow)
        if variant == "datafusion":
            return _run_datafusion(pairs_arrow, assignments_arrow, memory_limit)
        if variant == "polars":
            return _run_polars(pairs_arrow, assignments_arrow)
        raise ValueError(f"unknown variant {variant!r}")

    _one()  # warm
    walls: list[float] = []
    last: dict[str, Any] = {}
    for _ in range(runs):
        last = _one()
        walls.append(last["wall_s"])

    print(json.dumps({
        "variant": variant,
        "n_pairs": actual_pairs,
        "n_members": n_members,
        "n_clusters": int(n_clusters),
        "walls": walls,
        "peak_rss_mb": _peak_rss_mb(),
        "detail": {k: v for k, v in last.items() if k != "wall_s"},
    }), flush=True)
    return 0


def _bench_variant(variant: str, n: int, seed: int, runs: int,
                   memory_limit) -> dict[str, Any]:
    cmd = [
        sys.executable, os.path.abspath(__file__),
        "--child", variant, "--np", str(n), "--seed", str(seed),
        "--runs", str(runs),
    ]
    if memory_limit:
        cmd += ["--memory-limit", str(memory_limit)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    last_json = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            last_json = line
    if proc.returncode != 0 or last_json is None:
        # OOM / SIGKILL (-9) / 137 / any non-zero exit -> infeasibility point.
        return {
            "oom": True,
            "returncode": proc.returncode,
            "stderr_tail": proc.stderr.strip()[-2000:],
        }
    return json.loads(last_json)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--np", default="25000000,100000000",
                    help="Comma-separated target pair counts")
    ap.add_argument("--memory-limit", default="",
                    help="DataFusion spill-pool byte budget (empty = unbounded)")
    ap.add_argument("--variants", default="legacy,datafusion,polars",
                    help="Comma-separated subset of legacy,datafusion,polars")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--output", default=None)
    ap.add_argument("--child", choices=["legacy", "datafusion", "polars"],
                    default=None, help="Internal: run one variant in this process")
    args = ap.parse_args()

    runs = max(1, args.runs)
    mem = int(args.memory_limit) if str(args.memory_limit).strip() else None

    if args.child is not None:
        nps = [int(x.strip()) for x in args.np.split(",") if x.strip()]
        n = nps[0] if nps else 25_000_000
        return _run_child(args.child, n, args.seed, runs, mem)

    nps = [int(x.strip()) for x in args.np.split(",") if x.strip()]
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]

    print(f"variants={variants} np={nps} memory_limit={mem}", flush=True)
    print("3-way cluster-edge view bench on a HEAVY-TAILED partition (most "
          "clusters 2-5, long tail, a few oversized >100; sparse connectivity; "
          "~1% duplicate (a,b) pairs at a different score).", flush=True)
    print("NOTE: legacy input is PRE-DEDUPED to one row per (a,b) by MAX so the "
          "bottleneck-divergence rate is attributable to the LAST-WINS-vs-MAX "
          "TIE-BREAK rule, not input multiplicity. Divergence is measured on the "
          "RAW (pre-dedup) input.", flush=True)

    results = []
    divergence = []
    for n in nps:
        print(f"  target_pairs={n:,} ...", flush=True)
        row: dict[str, Any] = {"n_pairs": n}
        for variant in variants:
            res = _bench_variant(variant, n, args.seed, runs, mem)
            if res.get("oom"):
                row[variant] = {"oom": True, "returncode": res.get("returncode")}
                print(f"    {variant:>10}: OOM (rc={res.get('returncode')})",
                      flush=True)
            else:
                row["n_pairs"] = res["n_pairs"]
                row["n_members"] = res.get("n_members")
                row["n_clusters"] = res.get("n_clusters")
                row[variant] = {
                    "wall_s": statistics.median(res["walls"]),
                    "peak_rss_mb": res["peak_rss_mb"],
                    "detail": res.get("detail", {}),
                }
                print(f"    {variant:>10}: wall={row[variant]['wall_s']:.3f}s "
                      f"rss={row[variant]['peak_rss_mb']:.1f}MB", flush=True)
        results.append(row)

        # Bottleneck-divergence (in the parent; cheap Polars, no child needed).
        try:
            pairs_arrow, assignments_arrow = make_heavytailed(n, seed=args.seed)
            div = _divergence_rate(pairs_arrow, assignments_arrow)
            div["n_pairs"] = row.get("n_pairs", n)
            divergence.append(div)
            print(f"    divergence: {div['divergence_rate']*100:.4f}% "
                  f"({div['n_divergent']:,}/{div['n_clusters']:,} clusters)",
                  flush=True)
            del pairs_arrow, assignments_arrow
        except Exception as exc:  # noqa: BLE001
            print(f"    divergence ERROR {type(exc).__name__}: {exc}", flush=True)
            divergence.append({"n_pairs": n, "error": str(exc)})

    # --- markdown table ---
    def _cell(v: dict[str, Any] | None) -> tuple[str, str, str]:
        if v is None:
            return "n/a", "n/a", "n/a"
        if v.get("oom"):
            return "OOM", "OOM", "OOM"
        return f"{v['wall_s']:.3f}", f"{v['peak_rss_mb']:.1f}", "survived"

    lines = [
        "\n## bench-df-cluster-edges\n",
        "3-way cluster-edge view (legacy dict / embedded DataFusion stream / "
        "Polars lazy) on a heavy-tailed partition. wall = median over runs; "
        "peak RSS is per-variant (subprocess-isolated). DataFusion leg consumes "
        "the cid-sorted edge STREAM contiguously (no global dict).\n",
        f"| {'pairs':>13} | {'variant':>10} | {'wall s':>9} | "
        f"{'peak RSS MB':>12} | {'status':>9} |",
        f"| {'-'*13} | {'-'*10} | {'-'*9} | {'-'*12} | {'-'*9} |",
    ]
    for r in results:
        np_disp = r.get("n_pairs", 0)
        for variant in ("legacy", "datafusion", "polars"):
            if variant not in r:
                continue
            w, rss, st = _cell(r.get(variant))
            lines.append(
                f"| {np_disp:>13,} | {variant:>10} | {w:>9} | {rss:>12} | "
                f"{st:>9} |"
            )
    lines.append("\n**Bottleneck-divergence rate** (DataFusion MAX-dedup + "
                 "lexicographic tie-break vs legacy LAST-WINS, same membership, "
                 "RAW input):\n")
    lines.append(f"| {'pairs':>13} | {'divergence':>11} | "
                 f"{'divergent/clusters':>20} |")
    lines.append(f"| {'-'*13} | {'-'*11} | {'-'*20} |")
    for d in divergence:
        if d.get("error"):
            lines.append(f"| {d['n_pairs']:>13,} | {'ERROR':>11} | "
                         f"{d['error'][:20]:>20} |")
            continue
        rate = f"{d['divergence_rate']*100:.4f}%"
        frac = f"{d['n_divergent']:,}/{d['n_clusters']:,}"
        lines.append(f"| {d['n_pairs']:>13,} | {rate:>11} | {frac:>20} |")

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
            json.dump({"results": results, "divergence": divergence}, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
