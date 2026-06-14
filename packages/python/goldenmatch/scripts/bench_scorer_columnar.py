"""At-scale A/B prove bench for the scorer columnar pipeline.

Legacy list[tuple] scorer (GOLDENMATCH_COLUMNAR_PIPELINE=0) vs columnar DataFrame
scorer (=1), on an EXPLICIT single-weighted-fuzzy-matchkey config (auto-config is
ineligible). Each variant runs in its own subprocess for a clean peak RSS. The
verdict is: columnar faster + pair-parity where both run; columnar lower peak RSS
(the DataFrame pair representation is ~3x more compact than list[tuple], so at high
pair volume the legacy path OOMs first -- recorded as a result when it happens).

`make_workload` scales surname cardinality with N (bounded block size), so the
candidate volume is LINEAR in N -- a fixed surname pool makes the scorer O(N^2) and
wedges the box well before any signal (see make_workload). Target scales are 1M and
5M; 25M+ fuzzy RECORD-scoring is impractical (hours) and unnecessary -- the columnar
win is already visible at 1M/5M.

Local smoke: python scripts/bench_scorer_columnar.py --rows 2000 --runs 1
Workflow: bench-scorer-columnar.yml (large-new-64GB) passes --rows 1000000,5000000.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time

# Curated name pools -- surnames spread across soundex codes (blocking-hang guard).
_SURNAMES = [
    "Anderson", "Brown", "Clark", "Davis", "Evans", "Foster", "Garcia", "Harris",
    "Iverson", "Johnson", "King", "Lopez", "Martin", "Nguyen", "Oconnor", "Parker",
    "Quinn", "Roberts", "Smith", "Turner", "Underwood", "Vasquez", "Walker", "Young",
    "Zimmerman", "Bailey", "Coleman", "Dixon", "Edwards", "Fisher",
]
_FIRST = ["James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael",
          "Linda", "David", "Elizabeth", "William", "Susan", "Richard", "Karen"]


def make_workload(rows: int, dupe_rate: float = 0.2, seed: int = 7, block_target: int = 64):
    """Return a polars DataFrame of `rows` person records, ~dupe_rate of which are
    lightly-corrupted near-duplicates of an earlier record.

    Surname CARDINALITY scales with `rows` (~= n_base / block_target distinct
    surnames) so the exact-surname blocking keeps block size ~constant (~block_target)
    as N grows -- candidate-pair volume is then LINEAR in N. A FIXED surname pool would
    make block size = N / pool_size, so the per-block fuzzy cdist is O((N/pool)^2) and
    the whole scorer O(N^2): at 1M+ that builds multi-GB per-block score matrices and
    wedges/OOMs the box BEFORE the columnar-vs-legacy signal exists. Surname is the
    exact block key (always scores 1.0 within a block); given_name carries the fuzzy
    signal that drives the threshold decisions."""
    import random

    import polars as pl

    rng = random.Random(seed)
    n_base = max(1, int(rows * (1 - dupe_rate)))
    # Distinct, surname-shaped block keys sized to N (suffix keeps them unique).
    n_surnames = max(len(_SURNAMES), n_base // block_target)
    surname_pool = [f"{_SURNAMES[i % len(_SURNAMES)]}{i}" for i in range(n_surnames)]
    given: list[str] = []
    surname: list[str] = []
    for _ in range(n_base):
        given.append(rng.choice(_FIRST))
        surname.append(rng.choice(surname_pool))
    while len(given) < rows:
        src = rng.randrange(n_base)
        g = given[src]
        if len(g) > 3 and rng.random() < 0.5:
            i = rng.randrange(1, len(g) - 1)
            g = g[:i] + rng.choice("aeiou") + g[i + 1:]
        given.append(g)
        surname.append(surname[src])
    return pl.DataFrame({"given_name": given[:rows], "surname": surname[:rows]})


def make_config():
    """An EXPLICIT GoldenMatchConfig satisfying _is_columnar_eligible."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    mk = MatchkeyConfig(
        name="fuzzy_name",
        type="weighted",
        threshold=0.85,
        fields=[
            MatchkeyField(field="given_name", scorer="jaro_winkler", weight=0.4),
            MatchkeyField(field="surname", scorer="jaro_winkler", weight=0.6),
        ],
    )
    blocking = BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["surname"])])
    return GoldenMatchConfig(matchkeys=[mk], blocking=blocking)


def _peak_rss_mb():
    try:
        import resource
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    except Exception:
        return None


def _assert_eligible(config) -> None:
    from goldenmatch.core.pipeline import _is_columnar_eligible
    if not _is_columnar_eligible(config, config.get_matchkeys(), False):
        raise SystemExit("BENCH ERROR: config is NOT columnar-eligible; the A/B would be meaningless")


def _run_one(variant: str, df, config, runs: int) -> dict:
    os.environ["GOLDENMATCH_COLUMNAR_PIPELINE"] = "1" if variant == "columnar" else "0"
    os.environ["GOLDENMATCH_CLUSTER_FRAMES_OUT"] = "0"
    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    from goldenmatch import dedupe_df
    from goldenmatch.core.bench import bench_capture
    walls, scoring_walls = [], []
    pair_count = 0
    for _ in range(runs):
        t0 = time.time()
        with bench_capture() as rec:
            res = dedupe_df(df, config=config)
        walls.append(time.time() - t0)
        scoring_walls.append(float(rec.timings.get("fuzzy_score_blocks", 0.0)))
        pair_count = len(res.scored_pairs)
    return {
        "variant": variant, "rows": df.height,
        "wall_s": round(statistics.median(walls), 3),
        "scoring_wall_s": round(statistics.median(scoring_walls), 3),
        "peak_rss_mb": _peak_rss_mb(),
        "pair_count": pair_count,
    }


def _child(variant: str, rows: int, runs: int) -> int:
    df = make_workload(rows)
    config = make_config()
    _assert_eligible(config)
    print(json.dumps(_run_one(variant, df, config, runs)), flush=True)
    return 0


def _bench_variant(variant: str, rows: int, runs: int) -> dict:
    cmd = [sys.executable, os.path.abspath(__file__),
           "--child", variant, "--rows", str(rows), "--runs", str(runs)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    last = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            last = line
    if proc.returncode != 0 or last is None:
        return {"variant": variant, "rows": rows, "oom": True,
                "returncode": proc.returncode, "stderr_tail": proc.stderr[-400:]}
    return json.loads(last)


def _parity_check(rows: int) -> bool:
    from goldenmatch import dedupe_df
    df = make_workload(rows)
    config = make_config()
    _assert_eligible(config)

    def pairs(variant: str):
        os.environ["GOLDENMATCH_COLUMNAR_PIPELINE"] = "1" if variant == "columnar" else "0"
        os.environ["GOLDENMATCH_CLUSTER_FRAMES_OUT"] = "0"
        os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
        res = dedupe_df(df, config=config)
        return {(min(int(a), int(b)), max(int(a), int(b))) for a, b, _s in res.scored_pairs}

    return pairs("legacy") == pairs("columnar")


def _verdict(results: list, parity_ok: bool) -> str:
    lines = ["", "## Scorer columnar A/B verdict (advisory)"]
    lines.append(f"- pair-set parity (capped scale): {'PASS' if parity_ok else 'FAIL'}")
    for r in results:
        leg, col = r["legacy"], r["columnar"]
        n = r["rows"]
        if leg.get("oom"):
            lines.append(f"- {n:,}: legacy OOM (rc={leg.get('returncode')}); "
                         f"columnar wall={col.get('wall_s')}s rss={col.get('peak_rss_mb')}MB "
                         f"-> columnar SURVIVES where legacy can't")
        elif col.get("oom"):
            lines.append(f"- {n:,}: COLUMNAR OOM (unexpected) rc={col.get('returncode')}")
        else:
            faster = col["wall_s"] <= leg["wall_s"]
            count_ok = leg["pair_count"] == col["pair_count"]
            lines.append(f"- {n:,}: legacy {leg['wall_s']}s / columnar {col['wall_s']}s "
                         f"(scoring {leg['scoring_wall_s']}s vs {col['scoring_wall_s']}s); "
                         f"pair_count {'match' if count_ok else 'MISMATCH'}; "
                         f"columnar {'faster' if faster else 'SLOWER'}")
    lines.append("\nFlip-worthy if columnar is faster + parity where both run AND survives where legacy OOMs. "
                 "Advisory only -- maintainer flips from these numbers.")
    return "\n".join(lines)


def _table(results: list) -> str:
    rows = ["", "| rows | variant | wall s | scoring s | peak RSS MB | pairs |",
            "|---|---|---:|---:|---:|---:|"]
    for r in results:
        for v in ("legacy", "columnar"):
            d = r[v]
            if d.get("oom"):
                rows.append(f"| {r['rows']:,} | {v} | OOM | OOM | OOM | - |")
            else:
                rows.append(f"| {r['rows']:,} | {v} | {d['wall_s']} | {d['scoring_wall_s']} "
                            f"| {d.get('peak_rss_mb')} | {d['pair_count']:,} |")
    return "\n".join(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", default="1000000,5000000")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--child", choices=["legacy", "columnar"], default=None)
    ap.add_argument("--output", default=None)
    ap.add_argument("--summary-md", default=None)
    ap.add_argument("--parity-rows", type=int, default=2000)
    args = ap.parse_args(argv)

    if args.child:
        return _child(args.child, int(args.rows), args.runs)

    rows_list = [int(x.strip()) for x in args.rows.split(",") if x.strip()]
    parity_ok = _parity_check(min(args.parity_rows, rows_list[0]))
    results = []
    for n in rows_list:
        results.append({"rows": n,
                        "legacy": _bench_variant("legacy", n, args.runs),
                        "columnar": _bench_variant("columnar", n, args.runs)})
    text = _table(results) + "\n" + _verdict(results, parity_ok)
    print(text)
    payload = {"parity_ok": parity_ok, "results": results}
    if args.output:
        from pathlib import Path
        Path(args.output).write_text(json.dumps(payload, indent=2))
    if args.summary_md:
        with open(args.summary_md, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
