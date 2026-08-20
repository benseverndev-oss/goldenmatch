#!/usr/bin/env python3
"""Headroom of the zero-copy Arrow kernel entry vs the Vec entry.

## The question this answers

The Spark JVM path scores through row-shaped UDFs, and `spark/em.py` measured
that batching *inside Spark SQL* loses -- the container costs more than the
calls it saves -- concluding:

    Reducing crossings requires leaving the row-shaped UDF domain entirely
    (columnar / Arrow C Data Interface), which is a different and much larger
    change.

That larger change is a Catalyst-level columnar plan (Comet / Gluten / Photon
territory), and on Spark Connect it is not even injectable: `spark.sql.extensions`
is static cluster configuration, so `addArtifact` cannot ship one. Taking that
route costs the "install nothing on the cluster" property the jar exists for.

Before spending that, this measures the CEILING. `score_block_pairs_fs_arrow`
already exists in the kernel and `_score_fs_native_frame` already routes to it
behind `FS_SUPPORTS_ARROW`. So the two entries can be A/B'd directly, and the
gap between them bounds everything a columnar Spark integration could recover.

If the gap is ~1.2x, the Catalyst work is not worth its cost. If it is 3x+, it
has a mandate and a number.

## Why it drives the SHIPPED function

It calls `_score_fs_native_frame` with the module flag flipped, rather than
timing the two kernel entries directly. The flag is the real switch the shipped
code reads, so this measures the path users get -- including the marshalling on
each side, which is the thing under test. Timing the kernels alone would
measure the part nobody disputed.

Both arms are asserted to return IDENTICAL pairs. An arm that scored something
different would make the wall meaningless.

Usage:
    python scripts/bench_fs_arrow_vs_vec.py --rows 20000 --block-size 40 --repeat 5
"""

from __future__ import annotations

import argparse
import json
import statistics
import time


def build_frame(rows: int, block_size: int, seed: int = 42):
    """A blocked frame shaped like the FS scorer's input.

    Values are perturbed per (row, field) so the comparison vectors have real
    variety -- a fixture whose fields all agree would collapse to one pattern
    and score nothing like real data.
    """
    import random

    import pyarrow as pa

    rnd = random.Random(seed)
    first, last, city, zipc, dob = [], [], [], [], []
    for i in range(rows):
        ent = i // 2

        def perturb(base: str) -> str:
            r = rnd.random()
            if r < 0.60:
                return base
            if r < 0.80:
                return base[:-1]
            if r < 0.90:
                return base + "x"
            return ""

        first.append(perturb(f"ann{ent}"))
        last.append(perturb(f"lee{ent % max(rows // 6, 1)}"))
        city.append(perturb(f"city{ent % 40}"))
        zipc.append(perturb(f"z{ent % 500:03d}"))
        dob.append(perturb(f"19{ent % 80:02d}-01-01"))

    tbl = pa.table(
        {
            "__row_id__": pa.array(list(range(rows)), type=pa.int64()),
            "first": pa.array(first),
            "last": pa.array(last),
            "city": pa.array(city),
            "zip": pa.array(zipc),
            "dob": pa.array(dob),
        }
    )
    sizes = [block_size] * (rows // block_size)
    if rows % block_size:
        sizes.append(rows % block_size)
    return tbl, sizes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, default=20_000)
    ap.add_argument("--block-size", type=int, default=40)
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    from goldenmatch.core._native_loader import native_module

    mod = native_module()
    if mod is None:
        print("no native kernel in this environment; nothing to measure")
        return 2
    if not getattr(mod, "FS_SUPPORTS_ARROW", False):
        print("this kernel has no FS_SUPPORTS_ARROW; the arrow arm cannot run")
        return 2

    from goldenmatch.core import probabilistic as P

    tbl, sizes = build_frame(args.rows, args.block_size)
    n_pairs = sum(s * (s - 1) // 2 for s in sizes)
    print(f"[bench] {args.rows:,} rows, {len(sizes):,} blocks, {n_pairs:,} candidate pairs")

    mk, em = _fs_setup(P)

    def run(arm: str) -> tuple[float, list]:
        # The SHIPPED switch, flipped. `_score_fs_native_frame` reads
        # `FS_SUPPORTS_ARROW` off the module, so patching it exercises the real
        # branch rather than a reimplementation of it.
        real = mod.FS_SUPPORTS_ARROW
        try:
            if arm == "vec":
                mod.FS_SUPPORTS_ARROW = False  # type: ignore[attr-defined]
            t0 = time.perf_counter()
            out = P._score_fs_native_frame(tbl, sizes, mk, em)
            return time.perf_counter() - t0, out
        finally:
            mod.FS_SUPPORTS_ARROW = real  # type: ignore[attr-defined]

    results: dict[str, list[float]] = {"vec": [], "arrow": []}
    first_out: dict[str, list] = {}
    for i in range(args.repeat):
        for arm in ("vec", "arrow"):
            wall, out = run(arm)
            results[arm].append(wall)
            first_out.setdefault(arm, out)
            print(f"  run {i + 1} {arm:>5}: {wall:.4f}s ({len(out):,} pairs)")

    # IDENTICAL OUTPUT, or the wall means nothing.
    a = sorted((x, y, round(s, 9)) for x, y, s in first_out["vec"])
    b = sorted((x, y, round(s, 9)) for x, y, s in first_out["arrow"])
    identical = a == b
    if not identical:
        print(
            f"::error::arms DISAGREE: vec={len(a):,} arrow={len(b):,} pairs -- "
            f"the comparison is invalid"
        )

    vec, arw = statistics.median(results["vec"]), statistics.median(results["arrow"])
    print()
    print(f"  vec   median {vec:.4f}s")
    print(f"  arrow median {arw:.4f}s")
    print(f"  arrow is {vec / arw:.2f}x {'faster' if arw < vec else 'SLOWER'}")
    print(f"  identical output: {identical}")

    payload = {
        "rows": args.rows,
        "block_size": args.block_size,
        "n_pairs": n_pairs,
        "repeat": args.repeat,
        "identical_output": identical,
        "vec_median_s": round(vec, 6),
        "arrow_median_s": round(arw, 6),
        "speedup": round(vec / arw, 4),
        "vec_runs": [round(x, 6) for x in results["vec"]],
        "arrow_runs": [round(x, 6) for x in results["arrow"]],
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"  wrote {args.out}")
    return 0 if identical else 1


def _fs_setup(P):
    """A five-field FS matchkey plus a trained-looking EM result."""
    from goldenmatch import MatchkeyConfig, MatchkeyField

    fields = [
        MatchkeyField(field="first", scorer="jaro_winkler", weight=1.0),
        MatchkeyField(field="last", scorer="jaro_winkler", weight=1.0),
        MatchkeyField(field="city", scorer="jaro_winkler", weight=1.0),
        MatchkeyField(field="zip", scorer="exact", weight=1.0),
        MatchkeyField(field="dob", scorer="levenshtein", weight=1.0),
    ]
    for f in fields:
        f.levels = 3
        f.partial_threshold = 0.85
    mk = MatchkeyConfig(name="fs", type="probabilistic", fields=fields)
    mk.link_threshold = 0.5

    m = {f.field: [0.01, 0.09, 0.90] for f in fields}
    u = {f.field: [0.70, 0.25, 0.05] for f in fields}
    em = P.EMResult(
        m_probs=m,
        u_probs=u,
        match_weights={f.field: [-3.0, 0.0, 3.0] for f in fields},
        iterations=10,
        converged=True,
        proportion_matched=0.01,
    )
    return mk, em


if __name__ == "__main__":
    raise SystemExit(main())
