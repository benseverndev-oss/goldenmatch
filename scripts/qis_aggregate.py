#!/usr/bin/env python3
"""#510 oracle-delta aggregator: read a directory of per-rung JSONs (emitted by
quality_invariant_scale.py), compute each rung's F1 deltas vs the smallest-N
oracle rung, flag PASS/FAIL against the #510 targets, and emit a Markdown table
plus a one-line verdict. Pure Python -- no goldenmatch, no Ray.

    python scripts/qis_aggregate.py results_dir/ --out docs/quality-invariant-scale-table.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

# #510 pass targets (deltas vs the oracle rung).
TARGET_PAIRWISE = 0.005
TARGET_B_CUBED = 0.005
TARGET_CLUSTER = 0.010


def _f1(rung: dict, metric: str) -> float:
    return float(rung.get(metric, {}).get("f1", 0.0))


def build_report(rungs: list[dict]) -> dict:
    """Compute oracle deltas + verdict. `rungs` is a list of per-rung dicts.
    Oracle = the rung with the smallest `rows`."""
    rungs = sorted(rungs, key=lambda r: r.get("rows", 0))
    if not rungs:
        return {"rows": [], "markdown": "(no rungs)", "verdict_passed": True,
                "oracle_rows": None}
    oracle = rungs[0]
    o_pw, o_bc, o_cl = _f1(oracle, "pairwise"), _f1(oracle, "b_cubed"), _f1(oracle, "cluster")

    out_rows = []
    all_pass = True
    for r in rungs:
        d_pw = _f1(r, "pairwise") - o_pw
        d_bc = _f1(r, "b_cubed") - o_bc
        d_cl = _f1(r, "cluster") - o_cl
        passed = (abs(d_pw) <= TARGET_PAIRWISE and abs(d_bc) <= TARGET_B_CUBED
                  and abs(d_cl) <= TARGET_CLUSTER)
        # The oracle compares to itself (deltas 0) -> always PASS; keep it in.
        all_pass = all_pass and passed
        out_rows.append({
            "rows": r.get("rows"),
            "pairwise_f1": _f1(r, "pairwise"), "pairwise_delta": d_pw,
            "b_cubed_f1": _f1(r, "b_cubed"), "b_cubed_delta": d_bc,
            "cluster_f1": _f1(r, "cluster"), "cluster_delta": d_cl,
            "wall_s": r.get("wall_s", {}).get("total"),
            "rss_mb_peak": r.get("rss_mb_peak"),
            "scored_pairs": r.get("bench", {}).get("scored_pair_count"),
            "predicted_clusters": r.get("predicted_clusters"),
            "multi_member": r.get("multi_member_clusters"),
            "backend": r.get("backend", "auto"),
            "native": (r.get("native") or {}).get("available"),
            "passed": passed,
        })

    return {
        "oracle_rows": oracle.get("rows"),
        "rows": out_rows,
        "verdict_passed": all_pass,
        "markdown": _render_markdown(out_rows, oracle.get("rows"), all_pass),
    }


def _fmt(x, nd=4):
    if x is None:
        return ""
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _render_markdown(rows: list[dict], oracle_rows, all_pass: bool) -> str:
    header = ("| rows | pairwise F1 | Δpw | B-cubed F1 | Δbc | cluster F1 | Δcl | "
              "wall s | RSS MB | scored pairs | pred clusters | multi | backend | native | PASS |")
    sep = "|" + "|".join(["---"] * 15) + "|"
    lines = [header, sep]
    for r in rows:
        lines.append("| " + " | ".join([
            f"{r['rows']:,}",
            _fmt(r["pairwise_f1"]), _fmt(r["pairwise_delta"]),
            _fmt(r["b_cubed_f1"]), _fmt(r["b_cubed_delta"]),
            _fmt(r["cluster_f1"]), _fmt(r["cluster_delta"]),
            _fmt(r["wall_s"], 1), _fmt(r["rss_mb_peak"], 1),
            _fmt(r["scored_pairs"]), _fmt(r["predicted_clusters"]),
            _fmt(r["multi_member"]), _fmt(r["backend"]), _fmt(r["native"]),
            "✅" if r["passed"] else "❌",
        ]) + " |")
    verdict = ("**VERDICT: PASS** -- quality is invariant across the ladder "
               f"(oracle = {oracle_rows:,} rows; targets Δpairwise≤{TARGET_PAIRWISE}, "
               f"Δb-cubed≤{TARGET_B_CUBED}, Δcluster≤{TARGET_CLUSTER})."
               if all_pass else
               "**VERDICT: FAIL** -- at least one rung drifted beyond target; see ❌ rows.")
    return "\n".join(lines) + "\n\n" + verdict + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results_dir", type=Path, help="directory of per-rung *.json")
    ap.add_argument("--out", type=Path, default=None, help="write the Markdown table here")
    args = ap.parse_args(argv)
    rungs = [json.loads(p.read_text(encoding="utf-8"))
             for p in sorted(args.results_dir.glob("*.json"))]
    report = build_report(rungs)
    print(report["markdown"])
    if args.out:
        args.out.write_text(report["markdown"], encoding="utf-8")
    return 0 if report["verdict_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
