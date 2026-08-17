#!/usr/bin/env python3
"""Why the zero-config controller refuses, per shape and scale.

## Why this exists

The head-to-head panel records `gm_zeroconfig` as `refused` with one sentence of
error text, and that reads as a single behaviour -- "zero-config gives up at
100k rows". The four refusals actually observed disagree with each other:

    biblio  100k   sub-profile=cluster    stop_reason=BUDGET_ITERATIONS
    biblio  1M     sub-profile=blocking   stop_reason=BLOCKING_DEGENERATE
    person  100k   sub-profile=blocking   stop_reason=POLICY_SATISFIED
    person  1M     (timed out before reporting)

Three different sub-profiles and three different stop reasons. `POLICY_SATISFIED`
is the SUCCESS reason -- that search converged and the committed config still
graded RED -- so the stop reason is not the cause either.

`REFUSE_AT_N = 100_000` is real, but it is the ESCALATION point, not the cause:
below it a RED config warn-and-runs, at or above it the same RED config refuses.
So the question this answers is not "why 100k" but "why is the committed config
RED", which is a different question per shape.

## What it reports

The controller returns `(config, profile, history)`. This runs it with
`allow_red_config=True` so it hands the profile back instead of raising, then
prints each sub-profile's health verdict beside THE FIELD VALUES THAT DECIDE IT,
and names the rule that fired. The rules (complexity_profile.py):

    blocking RED  <- n_blocks == 0
                  <- largest block's share of total_comparisons
                     > max(0.10, 4 / n_blocks)                    [skew, #2628]
                  <- reduction_ratio < 0.5
    cluster  RED  <- cluster_size_max > 0.1 * n_rows
                  <- transitivity_rate < 0.85

`cluster_size_max > 0.1 * n_rows` is worth watching on the person shape: the
head-to-head measured non-singleton clusters averaging 7.98 members against a
truth of 2.40 at 1M, which is the same over-merge this rule exists to catch. If
it fires, the refusal is the guard working rather than a false alarm, and the
two open questions are one question.

Usage:
    python scripts/bench_er_headtohead/diagnose_zeroconfig_refusal.py \\
        --shapes person biblio --rows 100000 --out zeroconfig-refusal.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _fixture(shape: str, rows: int, seed: int, workdir: Path):
    """Records for one head-to-head shape, via the bench's own generator."""
    from generate_fixture import generate  # type: ignore

    out = workdir / f"{shape}_{rows}.parquet"
    truth = workdir / f"{shape}_{rows}_truth.parquet"
    if not out.exists():
        generate(rows=rows, dupe_rate=0.20, out=out, truth=truth,
                 seed=seed, batch=1_000_000, shape=shape)
    import pyarrow.parquet as pq

    return pq.read_table(out), pq.read_table(truth)


def _blocking_report(bp, n_rows: int) -> dict:
    n_blocks = getattr(bp, "n_blocks", 0)
    avg = n_rows / max(n_blocks, 1)
    p99 = getattr(bp, "block_sizes_p99", 0)
    rr = getattr(bp, "reduction_ratio", 0.0)
    singles = getattr(bp, "singleton_block_count", 0)
    # Pair-share of the biggest block: what the skew RED rule reads as of
    # #2628. The old rule was `p99 > 10 * avg`, which compares a tail
    # percentile to a MEAN pinned near 1 whenever blocking is fine-grained
    # (n_blocks -> n_rows) -- it fired on person@100k, whose largest block owned
    # 1.9% of the work with reduction 0.9757 and no singletons, and it missed a
    # single block owning 98.5% (which sits ABOVE p99, not at it). Skew is
    # dangerous when one block owns most of the WORK, and work is quadratic in
    # block size, so that is what the rule now measures. `p99_over_avg` stays
    # in the output as the retired signal, for comparison across runs.
    total_pairs = getattr(bp, "total_comparisons", 0) or 0
    biggest = getattr(bp, "block_sizes_max", 0) or 0
    biggest_pairs = biggest * (biggest - 1) // 2 if biggest >= 2 else 0
    share = biggest_pairs / total_pairs if total_pairs else 0.0
    skew_bar = max(0.10, 4.0 / n_blocks) if n_blocks else 0.10
    fired = []
    if n_blocks == 0:
        fired.append("n_blocks == 0")
    if share > skew_bar:
        fired.append(
            f"largest_block_pair_share {share:.4f} > bar {skew_bar:.4f}  [SKEW]"
        )
    if rr < 0.5:
        fired.append(f"reduction_ratio {rr:.4f} < 0.5")
    return {
        "n_blocks": n_blocks, "avg_block_size": round(avg, 2),
        "block_sizes_p50": getattr(bp, "block_sizes_p50", 0),
        "block_sizes_p95": getattr(bp, "block_sizes_p95", 0),
        "block_sizes_p99": p99,
        "block_sizes_max": biggest,
        "reduction_ratio": round(rr, 6),
        "singleton_block_count": singles,
        "singleton_fraction": round(singles / max(n_blocks, 1), 4),
        "total_comparisons": int(total_pairs),
        "largest_block_pairs": int(biggest_pairs),
        "largest_block_pair_share": round(share, 6) if total_pairs else None,
        "skew_bar": round(skew_bar, 6),
        "p99_over_avg": round(p99 / avg, 2) if avg else None,
        "p99_over_p50": (
            round(p99 / getattr(bp, "block_sizes_p50", 0), 2)
            if getattr(bp, "block_sizes_p50", 0) else None
        ),
        "red_rules_fired": fired,
    }


def _cluster_report(cp, n_rows: int) -> dict:
    cmax = getattr(cp, "cluster_size_max", 0)
    tr = getattr(cp, "transitivity_rate", 1.0)
    fired = []
    if n_rows > 0 and cmax > 0.1 * n_rows:
        fired.append(f"cluster_size_max {cmax} > 10% of n_rows ({0.1 * n_rows:.0f})")
    if tr < 0.85:
        fired.append(f"transitivity_rate {tr:.4f} < 0.85")
    return {
        "n_clusters": getattr(cp, "n_clusters", 0),
        "cluster_size_max": cmax,
        "cluster_size_max_pct_of_rows": round(100 * cmax / max(n_rows, 1), 3),
        "transitivity_rate": round(tr, 6),
        "oversized_cluster_count": getattr(cp, "oversized_cluster_count", 0),
        "bridge_edge_count": getattr(cp, "bridge_edge_count", 0),
        "measured_bridge_risk": getattr(cp, "measured_bridge_risk", None),
        "red_rules_fired": fired,
    }


def collect(shape: str, rows: int, seed: int, workdir: Path) -> dict:
    import polars as pl
    from goldenmatch.core.autoconfig_controller import (
        REFUSE_AT_N,
        AutoConfigController,
        ControllerBudget,
        _first_red_subprofile,
        resolve_planning_effort,
    )
    from goldenmatch.core.autoconfig_policy import HeuristicRefitPolicy

    records, _truth = _fixture(shape, rows, seed, workdir)
    df = pl.from_arrow(records)

    out: dict = {"shape": shape, "rows": rows, "refuse_at_n": REFUSE_AT_N,
                 "would_refuse_by_size": rows >= REFUSE_AT_N}
    t = time.perf_counter()
    # allow_red_config=True is the point: it makes the controller HAND BACK the
    # RED profile instead of raising, which is the only way to see the numbers
    # behind a refusal. It does not change what the controller decided.
    # Constructed the way `auto_configure_df` constructs it (heuristic policy,
    # dataset-sized budget) so the profile this reports is the one the shipped
    # path would produce. The LLM policy branch is deliberately skipped: it is
    # env-gated and non-deterministic, and a diagnosis has to be reproducible.
    ctrl = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget.for_dataset(df.height, resolve_planning_effort("normal")),
    )
    try:
        _cfg, profile, history = ctrl.run(df, allow_red_config=True)
    except Exception as e:  # noqa: BLE001 - one shape must not lose the rest
        out["error"] = f"{type(e).__name__}: {str(e)[:400]}"
        out["seconds"] = round(time.perf_counter() - t, 2)
        return out
    out["seconds"] = round(time.perf_counter() - t, 2)

    n = profile.data.n_rows
    healths = {
        "data": profile.data.health().name,
        "domain": profile.domain.health().name,
        "matchkey": profile.matchkey.health().name,
        "blocking": profile.blocking.health(n_rows=n).name,
        "scoring": profile.scoring.health().name,
        "cluster": profile.cluster.health(n_rows=n).name,
    }
    out["profiled_n_rows"] = n
    out["health"] = healths
    out["rollup"] = profile.health().name
    out["first_red_subprofile"] = _first_red_subprofile(profile)
    out["red_subprofiles"] = [k for k, v in healths.items() if v == "RED"]
    out["stop_reason"] = history.stop_reason.name if history.stop_reason else None
    out["iterations"] = len(getattr(history, "entries", []) or [])
    out["blocking"] = _blocking_report(profile.blocking, n)
    out["cluster"] = _cluster_report(profile.cluster, n)
    # `candidates_compared` is the field that tells the two scoring-RED causes
    # apart, and the first version of this report omitted it -- so a person@100k
    # run showed `n_pairs_scored=0` with no way to say which had happened:
    #
    #   candidates_compared == 0  -> the emitter never received a ScoringProfile,
    #       so `emitter.scoring or ScoringProfile()` fell back to the all-zero
    #       default. Scoring never ran. That RED is DOWNSTREAM of whatever
    #       stopped the sample pipeline.
    #   candidates_compared > 0   -> pairs were compared and NONE cleared the
    #       threshold. That RED is a real, independent signal about the
    #       threshold or the scorers, and fixing blocking will not clear it.
    #
    # Note `n_pairs_scored` counts pairs ABOVE the threshold, not pairs scored
    # (scorer.py builds it from `find_fuzzy_matches` output, which is already
    # filtered; autoconfig_policy.py renders the same field as
    # `n_pairs_above_threshold`). The name reads like the denominator and is
    # actually the numerator, which is what made the ambiguity above easy to
    # miss. Reported here under both names rather than silently renamed.
    sc = profile.scoring
    compared = getattr(sc, "candidates_compared", 0)
    above = getattr(sc, "n_pairs_scored", 0)
    out["scoring"] = {
        "candidates_compared": compared,
        "n_pairs_scored": above,
        "n_pairs_above_threshold": above,  # the same field, under its true name
        "above_threshold_rate": round(above / compared, 8) if compared else None,
        "scoring_ran": compared > 0,
        "dip_statistic": round(getattr(sc, "dip_statistic", 0.0), 6),
        "mass_above_threshold": round(getattr(sc, "mass_above_threshold", 0.0), 6),
        "mass_in_borderline": round(getattr(sc, "mass_in_borderline", 0.0), 6),
        "random_pair_above_threshold_rate":
            getattr(sc, "random_pair_above_threshold_rate", None),
        "red_cause": (
            None if (compared > 0 and getattr(sc, "mass_above_threshold", 0.0) > 0.0)
            else "scoring-never-ran (candidates_compared == 0)" if compared == 0
            else "nothing-cleared-threshold (candidates compared, mass == 0)"
        ),
    }
    return out


def render(reports: list[dict]) -> str:
    lines = ["# Zero-config refusal diagnosis", ""]
    lines.append("| shape | rows | rollup | RED sub-profiles | first RED | stop reason |")
    lines.append("|---|---:|---|---|---|---|")
    for r in reports:
        if r.get("error"):
            lines.append(f"| {r['shape']} | {r['rows']:,} | ERROR | - | - | {r['error'][:60]} |")
            continue
        lines.append(
            f"| {r['shape']} | {r['rows']:,} | {r['rollup']} | "
            f"{', '.join(r['red_subprofiles']) or 'none'} | {r['first_red_subprofile']} | "
            f"{r['stop_reason']} |"
        )
    lines.append("")
    for r in reports:
        if r.get("error"):
            continue
        lines.append(f"## {r['shape']} @ {r['rows']:,}")
        for key in ("blocking", "cluster"):
            fired = r[key]["red_rules_fired"]
            lines.append(f"- **{key}**: {'RED -> ' + '; '.join(fired) if fired else 'no RED rule fired'}")
            lines.append(f"  - `{json.dumps({k: v for k, v in r[key].items() if k != 'red_rules_fired'})}`")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shapes", nargs="+", default=["person", "biblio"])
    ap.add_argument("--rows", nargs="+", type=int, default=[100000])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workdir", default=".zeroconfig_diag")
    ap.add_argument("--out", default="zeroconfig-refusal.json")
    ap.add_argument("--summary-md", default="")
    args = ap.parse_args()

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    reports = []
    for rows in args.rows:
        for shape in args.shapes:
            r = collect(shape, rows, args.seed, workdir)
            reports.append(r)
            if r.get("error"):
                print(f"[zc] {shape}@{rows:,}: ERROR {r['error'][:160]}", flush=True)
            else:
                print(f"[zc] {shape}@{rows:,}: rollup={r['rollup']} "
                      f"RED={r['red_subprofiles']} stop={r['stop_reason']} "
                      f"({r['seconds']}s)", flush=True)
                for key in ("blocking", "cluster"):
                    for f in r[key]["red_rules_fired"]:
                        print(f"       {key} RED: {f}", flush=True)

    Path(args.out).write_text(json.dumps(reports, indent=2), encoding="utf-8")
    print(f"[zc] wrote {args.out}", flush=True)
    if args.summary_md:
        Path(args.summary_md).write_text(render(reports), encoding="utf-8")
        print(f"[zc] wrote {args.summary_md}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
