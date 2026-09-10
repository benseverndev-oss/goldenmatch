"""Can meta-blocking keep the recall extra blocking passes add while dropping the noise?

On historical_50k, blocking is the recall ceiling: a richer key set lifts reachable
recall from ~0.83 to ~0.93 at fewer candidates, yet adding those passes made F1
WORSE. FS estimates m, u and lambda FROM the candidate population, so a flood of
weak candidates degrades the model that scores the good ones (see the project note
on the blocking ceiling). Meta-blocking (Papadakis et al., TKDE) weights every
candidate pair by how the blocks it co-occurs in overlap, and prunes the weakest
edges BEFORE scoring -- reported at two orders of magnitude fewer comparisons for
under 3% pair-completeness loss.

This measures that, without touching the pipeline:

1. Rebuild each blocking pass with GoldenMatch's own ``build_blocks`` (a multi_pass
   config IS N static passes -- ``_build_multi_pass_blocks`` delegates each to
   ``_build_static_blocks``), so transforms and oversized-block auto-split are the
   shipped behaviour, not a lookalike.
2. KNOWN-POSITIVE: the rebuilt per-pass comparison total must equal the pipeline's
   own ``measure_blocking_profile(...).total_comparisons``. If it does not, the
   script stops -- a meta-blocking number over a wrong candidate set means nothing.
3. Build the blocking graph in duckdb: an edge per co-occurring pair, weighted by
   CBS (common blocks), JS (Jaccard of the two records' block sets) and ECBS (CBS
   scaled by each record's block rarity).
4. Prune with WEP, CEP, WNP and CNP (the node-centric schemes in both the
   "either endpoint" and "reciprocal" forms) and report, per scheme, candidates
   kept, pair completeness (share of true pairs kept) and pair quality (share of
   kept pairs that are true).

Usage (main venv, worktree packages on PYTHONPATH):
    python scripts/bench_er_headtohead/measure_meta_blocking.py \\
        [--extra "surname:lowercase,soundex"] [--out results.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def _members_for_passes(frame, blocking_cfg, keys):
    """(pass, block, rid) rows for blocks of size >= 2, plus per-pass comparisons."""
    from goldenmatch.core.blocker import build_blocks
    from goldenmatch.core.frame import to_frame

    passes, blocks, rids = [], [], []
    per_pass = []
    block_id = 0
    for p, key in enumerate(keys):
        static = blocking_cfg.model_copy(
            update={"strategy": "static", "keys": [key], "passes": None}
        )
        comparisons = 0
        n_blocks = 0
        for b in build_blocks(frame, static):
            ids = to_frame(b.materialize()).column("__row_id__").to_list()
            n = len(ids)
            if n < 2:
                continue
            comparisons += n * (n - 1) // 2
            n_blocks += 1
            passes.extend([p] * n)
            blocks.extend([block_id] * n)
            rids.extend(int(r) for r in ids)
            block_id += 1
        per_pass.append(
            {
                "pass": p,
                "fields": list(key.fields),
                "transforms": list(getattr(key, "transforms", None) or []),
                "blocks": n_blocks,
                "comparisons": comparisons,
            }
        )
    return passes, blocks, rids, per_pass


def _prune_report(con, n_gt: int, bc: int, n_nodes: int) -> list[dict]:
    all_edges, all_matches = con.execute("SELECT count(*), sum(is_match::INT) FROM ew").fetchone()
    rows = [
        {"scheme": "none (all passes)", "weight": "-", "kept": all_edges, "matches": all_matches}
    ]
    cep_k = bc // 2
    cnp_k = max(1, bc // max(1, n_nodes) - 1)
    for w in ("cbs", "js", "ecbs"):
        kept, m = con.execute(
            f"SELECT count(*), sum(is_match::INT) FROM ew WHERE {w} >= (SELECT avg({w}) FROM ew)"
        ).fetchone()
        rows.append({"scheme": "WEP", "weight": w, "kept": kept, "matches": m})

        kept, m = con.execute(
            f"SELECT count(*), sum(is_match::INT) FROM (SELECT is_match FROM ew ORDER BY {w} DESC, i, j LIMIT {cep_k})"
        ).fetchone()
        rows.append({"scheme": f"CEP (K={cep_k})", "weight": w, "kept": kept, "matches": m})

        con.execute(
            f"CREATE OR REPLACE TEMP TABLE inc AS "
            f"SELECT i AS node, i, j, {w} AS w FROM ew UNION ALL SELECT j AS node, i, j, {w} AS w FROM ew"
        )
        either, m_either, recip, m_recip = con.execute(
            "WITH mean AS (SELECT node, avg(w) AS mw FROM inc GROUP BY node), "
            f"k AS (SELECT ew.is_match, ew.{w} >= mi.mw AS ki, ew.{w} >= mj.mw AS kj "
            "FROM ew JOIN mean mi ON mi.node = ew.i JOIN mean mj ON mj.node = ew.j) "
            "SELECT sum((ki OR kj)::INT), sum(((ki OR kj) AND is_match)::INT), "
            "sum((ki AND kj)::INT), sum((ki AND kj AND is_match)::INT) FROM k"
        ).fetchone()
        rows.append({"scheme": "WNP (either)", "weight": w, "kept": either, "matches": m_either})
        rows.append({"scheme": "WNP (reciprocal)", "weight": w, "kept": recip, "matches": m_recip})

        either, m_either, recip, m_recip = con.execute(
            "WITH ranked AS (SELECT node, i, j, row_number() OVER (PARTITION BY node ORDER BY w DESC, i, j) AS rn FROM inc), "
            f"top AS (SELECT i, j, count(*) AS c FROM ranked WHERE rn <= {cnp_k} GROUP BY i, j) "
            "SELECT count(*), sum(ew.is_match::INT), sum((top.c = 2)::INT), sum((top.c = 2 AND ew.is_match)::INT) "
            "FROM top JOIN ew USING (i, j)"
        ).fetchone()
        rows.append(
            {"scheme": f"CNP (either, k={cnp_k})", "weight": w, "kept": either, "matches": m_either}
        )
        rows.append(
            {
                "scheme": f"CNP (reciprocal, k={cnp_k})",
                "weight": w,
                "kept": recip,
                "matches": m_recip,
            }
        )
        con.execute("DROP TABLE inc")

    for r in rows:
        r["matches"] = int(r["matches"] or 0)
        r["kept"] = int(r["kept"] or 0)
        r["pair_completeness"] = r["matches"] / n_gt if n_gt else 0.0
        r["pair_quality"] = r["matches"] / r["kept"] if r["kept"] else 0.0
        r["reduction_vs_all"] = 1.0 - r["kept"] / all_edges if all_edges else 0.0
    return rows


def run_scenario(name, df_rowid, cfg, keys, gt, duck_mem: str) -> dict:
    import duckdb
    import pyarrow as pa
    from goldenmatch.core.blocker import measure_blocking_profile
    from goldenmatch.core.frame import to_frame

    t0 = time.perf_counter()
    frame = to_frame(df_rowid)
    passes, blocks, rids, per_pass = _members_for_passes(frame, cfg.blocking, keys)
    rebuilt = sum(p["comparisons"] for p in per_pass)

    # KNOWN-POSITIVE: the reconstruction must be the pipeline's candidate set.
    probe_cfg = cfg.model_copy(
        update={"blocking": cfg.blocking.model_copy(update={"passes": list(keys)})}
    )
    profile = measure_blocking_profile(df_rowid, probe_cfg)
    expected = None if profile is None else int(profile.total_comparisons)
    if expected is None or expected != rebuilt:
        raise SystemExit(
            f"[{name}] RECONSTRUCTION MISMATCH: rebuilt {rebuilt:,} comparisons, "
            f"measure_blocking_profile says {expected}. Refusing to report meta-blocking "
            "numbers over a candidate set that is not the pipeline's."
        )

    tmp = Path(tempfile.gettempdir()) / "gm_metablock_duckdb"
    tmp.mkdir(exist_ok=True)
    con = duckdb.connect()
    con.execute(f"PRAGMA memory_limit='{duck_mem}'")
    con.execute(f"PRAGMA temp_directory='{tmp.as_posix()}'")
    members = pa.table(
        {
            "pass": pa.array(passes, pa.int32()),
            "block": pa.array(blocks, pa.int64()),
            "rid": pa.array(rids, pa.int64()),
        }
    )
    gt_tbl = pa.table(
        {
            "i": pa.array([a for a, _ in gt], pa.int64()),
            "j": pa.array([b for _, b in gt], pa.int64()),
        }
    )
    con.register("members_arrow", members)
    con.register("gt_arrow", gt_tbl)
    con.execute("CREATE TEMP TABLE m AS SELECT * FROM members_arrow")
    con.execute("CREATE TEMP TABLE g AS SELECT least(i, j) AS i, greatest(i, j) AS j FROM gt_arrow")
    n_blocks_total = con.execute("SELECT count(DISTINCT block) FROM m").fetchone()[0]
    bc = con.execute("SELECT count(*) FROM m").fetchone()[0]
    n_nodes = con.execute("SELECT count(DISTINCT rid) FROM m").fetchone()[0]
    con.execute("CREATE TEMP TABLE nb AS SELECT rid, count(*)::DOUBLE AS nb FROM m GROUP BY rid")
    con.execute(
        "CREATE TEMP TABLE e AS SELECT a.rid AS i, b.rid AS j, count(*)::DOUBLE AS cbs "
        "FROM m a JOIN m b ON a.block = b.block AND a.rid < b.rid GROUP BY 1, 2"
    )
    con.execute(
        "CREATE TEMP TABLE ew AS SELECT e.i, e.j, e.cbs, "
        "e.cbs / (ni.nb + nj.nb - e.cbs) AS js, "
        f"e.cbs * ln({n_blocks_total} / ni.nb) * ln({n_blocks_total} / nj.nb) AS ecbs, "
        "(g.i IS NOT NULL) AS is_match "
        "FROM e JOIN nb ni ON ni.rid = e.i JOIN nb nj ON nj.rid = e.j "
        "LEFT JOIN g ON g.i = e.i AND g.j = e.j"
    )
    cbs_hist = con.execute(
        "SELECT cbs::INT AS shared_blocks, count(*) AS pairs, sum(is_match::INT) AS true_pairs "
        "FROM ew GROUP BY 1 ORDER BY 1"
    ).fetchall()
    rows = _prune_report(con, len(gt), bc, n_nodes)
    con.close()
    return {
        "scenario": name,
        "passes": per_pass,
        "rebuilt_comparisons": rebuilt,
        "profile_comparisons": expected,
        "distinct_pairs": rows[0]["kept"],
        "gt_pairs": len(gt),
        "blocks": n_blocks_total,
        "block_assignments": bc,
        "nodes_in_blocks": n_nodes,
        "shared_block_histogram": [
            {"shared_blocks": s, "pairs": int(p), "true_pairs": int(t or 0)} for s, p, t in cbs_hist
        ],
        "schemes": rows,
        "seconds": round(time.perf_counter() - t0, 1),
    }


def _print(result: dict) -> None:
    print(f"\n=== {result['scenario']} ===")
    print(
        f"comparisons: rebuilt {result['rebuilt_comparisons']:,} == profile {result['profile_comparisons']:,} (known-positive OK)"
    )
    print(
        f"distinct candidate pairs {result['distinct_pairs']:,}; true pairs {result['gt_pairs']:,}; "
        f"blocks {result['blocks']:,}; {result['seconds']}s"
    )
    for p in result["passes"]:
        print(
            f"  pass {p['pass']}: {p['fields']} {p['transforms']}  blocks={p['blocks']:,} comparisons={p['comparisons']:,}"
        )
    print("  pairs by number of shared blocks (CBS):")
    for h in result["shared_block_histogram"]:
        share = h["true_pairs"] / h["pairs"] if h["pairs"] else 0.0
        print(
            f"    {h['shared_blocks']:>2}: {h['pairs']:>10,} pairs, {h['true_pairs']:>8,} true ({share:.3f})"
        )
    print(f"  {'scheme':28s} {'weight':6s} {'kept':>11s} {'reduction':>9s} {'PC':>7s} {'PQ':>7s}")
    for r in result["schemes"]:
        print(
            f"  {r['scheme']:28s} {r['weight']:6s} {r['kept']:>11,} {r['reduction_vs_all']:>9.3f} "
            f"{r['pair_completeness']:>7.4f} {r['pair_quality']:>7.4f}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--extra",
        action="append",
        default=[],
        help='extra pass as "field:transform1,transform2" (repeatable) for the second scenario',
    )
    ap.add_argument("--out", default=None, help="write results as JSON")
    ap.add_argument("--duckdb-memory", default="3GB")
    args = ap.parse_args()

    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    from goldenmatch.config.schemas import BlockingKeyConfig
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df

    from scripts.autoconfig_quality import datasets as D

    loaded = D._historical_50k()
    if loaded is None:
        print("historical_50k parquet not present", file=sys.stderr)
        return 2
    df, gt = loaded
    cfg = auto_configure_probabilistic_df(df)
    df_rowid = df.with_row_index("__row_id__") if "__row_id__" not in df.columns else df
    base_keys = list(cfg.blocking.resolved_keys())
    print(
        f"historical_50k: {df.height:,} rows, {len(gt):,} true pairs; auto-config blocking "
        f"{cfg.blocking.strategy}, max_block_size={cfg.blocking.max_block_size}, "
        f"skip_oversized={cfg.blocking.skip_oversized}, {len(base_keys)} passes"
    )

    results = [run_scenario("auto-config passes", df_rowid, cfg, base_keys, gt, args.duckdb_memory)]
    _print(results[-1])
    if args.extra:
        extra_keys = []
        for spec in args.extra:
            field, _, transforms = spec.partition(":")
            extra_keys.append(
                BlockingKeyConfig(
                    fields=[field], transforms=[t for t in transforms.split(",") if t]
                )
            )
        name = "auto-config + " + ", ".join(args.extra)
        results.append(
            run_scenario(name, df_rowid, cfg, base_keys + extra_keys, gt, args.duckdb_memory)
        )
        _print(results[-1])
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
