"""Stage 0 baseline for the **in-engine dedupe** scope.

Design: ``docs/design/2026-07-06-goldenpipe-in-engine-dedupe-scope.md``.

The question this gates: **is the DuckDB<->Python crossing a material fraction of a
real DEDUPE, or does the (rapidfuzz) scoring compute swallow it?**

Phase C measured the crossing at ~89% of a *plain projection* pull path. But dedupe
is compute-heavy: scoring is O(candidate pairs) of rapidfuzz work, and that compute
runs at the SAME speed whether the kernel is called from Python-over-Arrow or from an
in-engine UDF (both are ``score-core`` / ``graph-core`` under the hood). So the ONLY
thing an in-engine dedupe can save is the **crossing** — pulling the warehouse table
into Python (ingress) and pushing the result back (egress). Therefore:

    crossing_fraction = (ingress + egress) / total   ==   the CEILING on any
                                                          in-engine-dedupe speedup.

This probe measures that ceiling for a **warehouse-resident** dedupe (data starts in
a DuckDB table; result written back to a DuckDB table — the only scenario where
in-engine dedupe could win). If the ceiling is small, in-engine dedupe is a Phase-B
"don't build"; if large, it justifies Phase 1 (Postgres) / Phase 2 (DuckDB cdylib).

Run:  python benchmarks/stage0_inengine_dedupe_probe.py
No external deps beyond goldenmatch + duckdb + polars (already required for Phase C).
"""
from __future__ import annotations

import random
import statistics
import time

import duckdb
import goldenmatch
import polars as pl

# --- deterministic synthetic people data with injected duplicates -----------

# Procedurally expand the name pools so blocking on ``last_name`` yields realistic,
# BOUNDED block sizes (avg ~ n / |surnames|). A tiny pool would make one low-card
# block key blow up to O(block^2) billions of pairs — an artifact, not a dedupe.
_SYL_A = ["smi", "john", "will", "brow", "jon", "gar", "mil", "dav", "rod", "mar",
          "her", "lop", "wil", "lee", "walk", "hall", "young", "king", "wright",
          "scott", "green", "adam", "baker", "nel", "car", "mit", "per", "rob",
          "tur", "phil", "camp", "par", "evan", "edw", "coll", "stew", "san", "mor"]
_SYL_B = ["th", "son", "iams", "wn", "es", "cia", "ler", "is", "riguez", "tinez",
          "nandez", "ez", "ford", "man", "ton", "sen", "by", "ner", "ley", "combe"]
_LAST = sorted({a.capitalize() + b for a in _SYL_A for b in _SYL_B})  # ~760 surnames
_FIRST = ["James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael",
          "Linda", "William", "Elizabeth", "David", "Barbara", "Richard", "Susan",
          "Joseph", "Jessica", "Thomas", "Sarah", "Charles", "Karen", "Daniel",
          "Nancy", "Matthew", "Lisa", "Anthony", "Betty", "Mark", "Sandra"]
_CITY = ["Springfield", "Franklin", "Greenville", "Bristol", "Clinton",
         "Fairview", "Salem", "Madison", "Georgetown", "Arlington"]


def _noise(s: str, rng: random.Random) -> str:
    """Light typo/case/whitespace noise to make fuzzy scoring do real work."""
    r = rng.random()
    if r < 0.30:
        return s.upper()
    if r < 0.50:
        return f"  {s} "
    if r < 0.70 and len(s) > 3:  # single-char typo
        i = rng.randrange(1, len(s) - 1)
        return s[:i] + s[i + 1] + s[i] + s[i + 2:]
    return s


def make_people(n_rows: int, seed: int = 7) -> pl.DataFrame:
    """~35% of rows are noisy duplicates of an earlier row (realistic dup rate)."""
    rng = random.Random(seed)
    rows: list[dict] = []
    while len(rows) < n_rows:
        if rows and rng.random() < 0.35:  # duplicate an existing entity
            base = rng.choice(rows)
            rows.append({
                "first_name": _noise(base["first_name"], rng),
                "last_name": _noise(base["last_name"], rng),
                "email": base["email"],
                "city": _noise(base["city"], rng),
            })
        else:  # fresh entity
            fn, ln = rng.choice(_FIRST), rng.choice(_LAST)
            rows.append({
                "first_name": fn,
                "last_name": ln,
                "email": f"{fn.lower()}.{ln.lower()}{rng.randrange(1000)}@mail.com",
                "city": rng.choice(_CITY),
            })
    return pl.DataFrame(rows[:n_rows])


def _dedupe(df: pl.DataFrame):
    # Explicit config (not zero-config): auto-config profiling is the "smart pipe"
    # that stays host regardless, so excluding it keeps the crossing ceiling honest
    # — we compare the crossing against ONLY the block+score+cluster compute that an
    # in-engine path would actually replace.
    return goldenmatch.dedupe_df(
        df,
        fuzzy={"first_name": 0.85, "last_name": 0.85},
        blocking=["last_name"],
        threshold=0.85,
    )


def probe_once(n_rows: int) -> dict:
    con = duckdb.connect()
    df = make_people(n_rows)
    con.register("src_view", df.to_arrow())
    con.execute("CREATE TABLE people AS SELECT * FROM src_view")
    con.unregister("src_view")

    # ingress: warehouse table -> Python (the pull an in-engine path avoids)
    t0 = time.perf_counter()
    pulled = con.sql("SELECT * FROM people").pl()
    t1 = time.perf_counter()

    # compute: block + score + cluster (runs the same in-engine or in-process)
    result = _dedupe(pulled)
    t2 = time.perf_counter()

    # egress: result -> warehouse table (the push-back an in-engine path avoids)
    out = result.golden if getattr(result, "golden", None) is not None else pulled
    con.register("out_view", out.to_arrow())
    con.execute("CREATE TABLE deduped AS SELECT * FROM out_view")
    con.unregister("out_view")
    t3 = time.perf_counter()
    con.close()

    ingress, compute, egress = t1 - t0, t2 - t1, t3 - t2
    total = ingress + compute + egress
    return {
        "rows": n_rows,
        "ingress": ingress,
        "compute": compute,
        "egress": egress,
        "total": total,
        "crossing": ingress + egress,
        "crossing_pct": 100.0 * (ingress + egress) / total,
    }


def probe(n_rows: int, trials: int = 2) -> dict:
    probe_once(n_rows)  # warm up (native kernel load, rapidfuzz JIT of tables)
    runs = [probe_once(n_rows) for _ in range(trials)]
    med = {k: statistics.median(r[k] for r in runs) for k in
           ("ingress", "compute", "egress", "total", "crossing", "crossing_pct")}
    med["rows"] = n_rows
    return med


def main() -> None:
    print(f"goldenmatch {goldenmatch.__version__}  duckdb {duckdb.__version__}  "
          f"|surnames|={len(_LAST)}\n", flush=True)
    print(f"{'rows':>10} {'ingress':>9} {'compute':>9} {'egress':>9} "
          f"{'total':>9} {'crossing':>9} {'crossing%':>10}", flush=True)
    print("-" * 72, flush=True)
    results = []
    for n in (10_000, 40_000, 100_000):
        m = probe(n)
        results.append(m)
        print(f"{m['rows']:>10} {m['ingress']*1e3:>8.1f}m {m['compute']*1e3:>8.1f}m "
              f"{m['egress']*1e3:>8.1f}m {m['total']*1e3:>8.1f}m "
              f"{m['crossing']*1e3:>8.1f}m {m['crossing_pct']:>9.2f}%", flush=True)
    print("-" * 72, flush=True)
    print("\ncrossing% is the CEILING on any in-engine-dedupe speedup for a")
    print("warehouse-resident dedupe. Compare to Phase C's 89% (plain projection).")


if __name__ == "__main__":
    main()
