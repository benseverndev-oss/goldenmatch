"""Measure-first bench: survivorship slow path vs vectorized floor.

Synthesizes a __cluster_id__-tagged multi-member frame directly (no
blocking/dedupe), then measures two variants in separate subprocesses for
clean peak RSS:

  slow  -- the real survivorship branch: sort -> build_resolution_order ->
            partition_by -> per-cluster resolve_cluster. Per-phase timers
            isolate sort / partition / loop costs.
  floor -- plain most_complete config routed through the vectorized
           _build_golden_records_polars_native path (asserted eligible before
           the run; the bench is meaningless if this assertion fails).

The verdict applies the distributed-plan-style kill criterion:
  - tax (slow_total - floor_total) >= 25% of slow_total, AND
  - recoverable fraction (partition_wall + loop_wall) >= 25% of slow_total,
    AND RSS delta <= 15%.
GO -> pursue Phase-2 columnar survivorship rewrite.
NO-GO -> keep the slow path (no evidence the rewrite pays).

Local smoke:  python scripts/bench_survivorship_columnar.py --rows 1000 --runs 1
CI workflow:  bench-survivorship-columnar.yml (large-new-64GB)
              passes --rows 1000000,5000000 --runs 3
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time

_STATES = ["CA", "NY", "TX", "FL", "IL", "WA", "MA", "OH"]
_SOURCES = ["crm", "billing", "events"]


# ---------------------------------------------------------------------------
# Workload synthesis
# ---------------------------------------------------------------------------

def make_clustered_workload(rows: int, avg_cluster_size: int = 3, seed: int = 7):
    """Return a polars DataFrame of `rows` tagged multi-member records.

    Every cluster has at least 2 members (survivorship only fires on
    multi-member clusters). Intra-cluster nulls give the resolver real work
    across all levers (group_winner, most_recent, source_priority, validate).
    The frame includes `__source__` so source_priority has data to read.
    """
    import random

    import polars as pl

    rnd = random.Random(seed)
    recs = []
    rid = 0
    cid = 0
    while len(recs) < rows:
        size = max(2, avg_cluster_size)  # all multi-member
        cid += 1
        base_zip = f"{10000 + cid % 89999:05d}"
        for k in range(size):
            if len(recs) >= rows:
                break
            recs.append({
                "__cluster_id__": cid,
                "__row_id__": rid,
                "first_name": rnd.choice(["Jon", "John", "Jonathan"]),
                "last_name": f"Sev{cid % 997}",
                "street": None if k == 0 else f"{cid % 9999} Main St",
                "city": "Springfield" if k != 1 else None,
                "state": _STATES[cid % len(_STATES)],
                "zip": None if k == 1 else base_zip,
                "phone": None if k == 2 else f"212555{cid % 9999:04d}",
                "updated_at": f"2024-{1 + (k % 12):02d}-01",
                "__source__": _SOURCES[(cid + k) % len(_SOURCES)],
            })
            rid += 1
    return pl.DataFrame(recs[:rows])


# ---------------------------------------------------------------------------
# Config builders
# ---------------------------------------------------------------------------

def make_survivorship_config():
    """A realistic mixed survivorship config: field group + conditional rule."""
    from goldenmatch.config.schemas import (
        GoldenFieldRule,
        GoldenGroupRule,
        GoldenRulesConfig,
    )

    return GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[
            GoldenGroupRule(
                name="mailing_address",
                columns=["street", "city", "state", "zip"],
                strategy="most_complete",
            ),
        ],
        field_rules={
            "phone": [
                GoldenFieldRule(
                    when="state in ['CA','NY']",
                    strategy="most_recent",
                    date_column="updated_at",
                    validate="nanp",
                ),
                GoldenFieldRule(
                    strategy="source_priority",
                    source_priority=["crm", "billing"],
                ),
            ],
        },
    )


def make_floor_config():
    """Plain most_complete -- no levers -> native-eligible."""
    from goldenmatch.config.schemas import GoldenRulesConfig

    return GoldenRulesConfig(default_strategy="most_complete")


def assert_floor_eligible(floor_rules) -> None:
    """Raise if the floor config would NOT route to the vectorized native path.

    A failed assertion means the 'floor' variant is not actually the fast path,
    so the measured tax is garbage. Abort rather than silently produce a wrong
    verdict.
    """
    from goldenmatch.core.golden import _polars_native_eligible, _survivorship_active

    assert not _survivorship_active(floor_rules), \
        "floor config must be non-survivorship"
    assert _polars_native_eligible(floor_rules, None), \
        "floor config must hit the vectorized native path"


# ---------------------------------------------------------------------------
# Measurement core
# ---------------------------------------------------------------------------

def run_slow(multi_df, rules, runs: int) -> dict:
    """Reconstruct the survivorship branch with per-phase timers.

    Mirrors build_golden_records_batch's survivorship branch exactly:
      1. sort by __cluster_id__
      2. build_resolution_order
      3. partition_by(__cluster_id__, maintain_order=True)
      4. per-cluster resolve_cluster(provenance=False)

    Returns median wall over `runs` with a phase breakdown.
    """
    from goldenmatch.core.golden import _is_internal
    from goldenmatch.core.survivorship.conditions import build_resolution_order
    from goldenmatch.core.survivorship.resolve import resolve_cluster

    totals, sorts, parts, loops = [], [], [], []
    rows_out = 0
    for _ in range(runs):
        t0 = time.perf_counter()

        # Phase 1: sort (same as production branch)
        s_sorted = multi_df.sort("__cluster_id__")
        t_sort = time.perf_counter()

        # Phase 2: build resolution order + partition (combined as "partition" phase)
        user_cols = [
            c for c in s_sorted.columns
            if not _is_internal(c) and c != "__cluster_id__"
        ]
        order = build_resolution_order(rules.field_rules, rules.field_groups, user_cols)
        partitions = s_sorted.partition_by("__cluster_id__", maintain_order=True)
        t_part = time.perf_counter()

        # Phase 3: per-cluster loop (the O(clusters) Python loop)
        out = []
        for cdf in partitions:
            cid = cdf["__cluster_id__"][0]
            rec, _ = resolve_cluster(cdf, rules, order, cluster_id=int(cid))
            out.append(rec)
        t_loop = time.perf_counter()

        rows_out = len(out)
        totals.append(t_loop - t0)
        sorts.append(t_sort - t0)
        parts.append(t_part - t_sort)
        loops.append(t_loop - t_part)

    return {
        "total_wall_s": round(statistics.median(totals), 4),
        "sort_wall_s": round(statistics.median(sorts), 4),
        "partition_wall_s": round(statistics.median(parts), 4),
        "loop_wall_s": round(statistics.median(loops), 4),
        "n_clusters": multi_df["__cluster_id__"].n_unique(),
        "rows_out": rows_out,
    }


def run_floor(multi_df, floor_rules, runs: int) -> dict:
    """Time the vectorized native path (asserts eligibility first)."""
    from goldenmatch.core.golden import build_golden_records_batch

    assert_floor_eligible(floor_rules)
    walls = []
    rows_out = 0
    for _ in range(runs):
        t0 = time.perf_counter()
        out = build_golden_records_batch(multi_df, floor_rules)
        walls.append(time.perf_counter() - t0)
        rows_out = len(out)
    return {
        "total_wall_s": round(statistics.median(walls), 4),
        "rows_out": rows_out,
    }


# ---------------------------------------------------------------------------
# RSS
# ---------------------------------------------------------------------------

def _peak_rss_mb():
    """Return peak RSS in MB for this process, or None on non-Unix."""
    try:
        import resource
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

def verdict(slow: dict, floor: dict) -> str:
    """Compute the GO/NO-GO verdict from slow + floor timing dicts.

    Criterion (all must hold for GO):
      - tax (slow_total - floor_total) >= 25% of slow_total
      - recoverable (partition_wall + loop_wall) >= 25% of slow_total
        (coarse proxy; over-counts the truly-vectorizable portion because
        loop_wall includes the non-vectorizable conditional eval -- makes
        NO-GO robust and GO optimistic; de-risk a coarse GO with py-spy)
      - the vectorized direction does not REGRESS RSS (or either side None)
    """
    total = slow["total_wall_s"]
    tax = max(0.0, total - floor["total_wall_s"])
    recoverable = slow.get("partition_wall_s", 0.0) + slow.get("loop_wall_s", 0.0)
    frac = recoverable / total if total else 0.0
    # RSS gate: a vectorized rewrite must not BLOW UP RSS (the prior columnar
    # A/B failure mode). The fast-path floor is the vectorized proxy; compare
    # IT to the slow path -- if the vectorized direction does not use materially
    # MORE RSS than slow, it is RSS-safe. (The slow path being RSS-HEAVY vs the
    # floor is a reason TO rewrite, not against -- so we must NOT require
    # slow <= floor, which would invert the gate and veto exactly the
    # RSS-heavy slow paths a rewrite fixes.)
    rss_ok = (
        floor.get("peak_rss_mb") is None
        or slow.get("peak_rss_mb") is None
        or floor["peak_rss_mb"] <= slow["peak_rss_mb"] * 1.15
    )
    go = (
        (tax / total >= 0.25 if total else False)
        and frac >= 0.25
        and rss_ok
    )
    label = "GO" if go else "NO-GO"
    detail = (
        "Vectorizable cost dominates and clears the 25-30% bar -> pursue Phase-2 rewrite."
        if go else
        "Tax below bar or not localized to a vectorizable phase, or RSS regressed -> keep the slow path."
    )
    return (
        f"VERDICT: {label} "
        f"(tax={tax:.3f}s {100 * tax / total:.0f}% of slow, "
        f"recoverable~{100 * frac:.0f}%, rss_ok={rss_ok}). "
        + detail
    )


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------

def _table(results: list) -> str:
    rows = [
        "",
        "## survivorship-columnar bench",
        "",
        "| rows | variant | total s | sort s | partition s | loop s | peak RSS MB | row count |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        n = r["rows"]
        for variant, d in (("slow", r["slow"]), ("floor", r["floor"])):
            if d.get("oom"):
                rows.append(f"| {n:,} | {variant} | OOM | - | - | - | - | - |")
            else:
                rows.append(
                    f"| {n:,} | {variant}"
                    f" | {d.get('total_wall_s', '-')}"
                    f" | {d.get('sort_wall_s', '-')}"
                    f" | {d.get('partition_wall_s', '-')}"
                    f" | {d.get('loop_wall_s', '-')}"
                    f" | {d.get('peak_rss_mb', 'N/A')}"
                    f" | {d.get('rows_out', '-')} |"
                )
        rows.append(f"| {n:,} | verdict | {r['verdict']} | | | | | |")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Child entrypoint (subprocess per variant for clean peak RSS)
# ---------------------------------------------------------------------------

def _child_slow(rows: int, runs: int, seed: int, avg_cluster_size: int) -> int:
    df = make_clustered_workload(rows=rows, avg_cluster_size=avg_cluster_size, seed=seed)
    rules = make_survivorship_config()
    result = run_slow(df, rules, runs)
    result["peak_rss_mb"] = _peak_rss_mb()
    print(json.dumps(result), flush=True)
    return 0


def _child_floor(rows: int, runs: int, seed: int, avg_cluster_size: int) -> int:
    df = make_clustered_workload(rows=rows, avg_cluster_size=avg_cluster_size, seed=seed)
    floor_rules = make_floor_config()
    result = run_floor(df, floor_rules, runs)
    result["peak_rss_mb"] = _peak_rss_mb()
    print(json.dumps(result), flush=True)
    return 0


def _bench_variant(variant: str, rows: int, runs: int, seed: int,
                   avg_cluster_size: int) -> dict:
    """Spawn a child subprocess for the given variant; return the parsed JSON dict."""
    cmd = [
        sys.executable,
        os.path.abspath(__file__),
        "--child", variant,
        "--rows", str(rows),
        "--runs", str(runs),
        "--seed", str(seed),
        "--avg-cluster-size", str(avg_cluster_size),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    last = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            last = line
    if proc.returncode != 0 or last is None:
        return {
            "variant": variant,
            "rows": rows,
            "oom": True,
            "returncode": proc.returncode,
            "stderr_tail": proc.stderr[-400:],
        }
    return json.loads(last)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Survivorship slow-path vs floor vectorized bench."
    )
    ap.add_argument("--rows", default="1000000,5000000",
                    help="Comma-separated row counts")
    ap.add_argument("--runs", type=int, default=3,
                    help="Repetitions per measurement (median)")
    ap.add_argument("--seed", type=int, default=7,
                    help="RNG seed for workload synthesis (same across both variants)")
    ap.add_argument("--avg-cluster-size", type=int, default=3,
                    dest="avg_cluster_size",
                    help="Average members per cluster (same across both variants)")
    ap.add_argument("--child", choices=["slow", "floor"], default=None,
                    help="Internal: run as a child subprocess for a single variant")
    ap.add_argument("--output", default=None,
                    help="Write JSON results to this path")
    args = ap.parse_args(argv)

    # Child mode: run one variant and print JSON to stdout.
    if args.child == "slow":
        return _child_slow(
            rows=int(args.rows),
            runs=args.runs,
            seed=args.seed,
            avg_cluster_size=args.avg_cluster_size,
        )
    if args.child == "floor":
        return _child_floor(
            rows=int(args.rows),
            runs=args.runs,
            seed=args.seed,
            avg_cluster_size=args.avg_cluster_size,
        )

    # Parent mode: spawn a child per variant per scale and aggregate.
    rows_list = [int(x.strip()) for x in args.rows.split(",") if x.strip()]
    results = []
    for n in rows_list:
        slow_d = _bench_variant("slow", n, args.runs, args.seed, args.avg_cluster_size)
        floor_d = _bench_variant("floor", n, args.runs, args.seed, args.avg_cluster_size)
        v = verdict(slow_d, floor_d) if not slow_d.get("oom") and not floor_d.get("oom") else "OOM"
        results.append({"rows": n, "slow": slow_d, "floor": floor_d, "verdict": v})

    text = _table(results)
    for r in results:
        if not r["slow"].get("oom") and not r["floor"].get("oom"):
            print(r["verdict"])
    print(text)

    if args.output:
        from pathlib import Path
        Path(args.output).write_text(json.dumps(results, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
