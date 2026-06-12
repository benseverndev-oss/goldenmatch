"""Low-scale bucket fast-path benchmark + parity harness.

Targets the interpreted per-pair loop in score_buckets._score_one_bucket_fast
(reached when the planner picks `bucket` -- i.e. native is enabled -- but the
field scorer isn't native-eligible, e.g. soundex_match). Compares that loop
against the vectorized batched-matrix lane (_score_block_vec, gated by
GOLDENMATCH_BUCKET_VEC_MIN/MAX) on two axes:

  * parity  -- the full dedupe result (cluster count, duplicate count) must be
               identical with the lane forced ON vs forced OFF. This is the
               end-to-end complement to the byte-parity unit test
               (tests/test_score_buckets_vectorized_fallback.py).
  * wall    -- median wall of the whole dedupe, lane ON vs OFF, so the >=2x
               ship rule (docs/superpowers/specs/2026-05-02-performance-audit-
               checklist.md) is checked on the real shape, not a micro-bench.

The lane is selected by env, read per score_buckets call, so a single process
can A/B by flipping GOLDENMATCH_BUCKET_VEC_MIN between runs (no reimport).

Usage:
    python scripts/bench_lowscale.py --rows 20000 --scorer soundex_match
    python scripts/bench_lowscale.py --rows 20000 --scorer jaro_winkler --sweep
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

import polars as pl  # noqa: E402
from fixtures.realistic_person import realistic_person_df  # noqa: E402
from goldenmatch import dedupe_df  # noqa: E402
from goldenmatch.config.schemas import (  # noqa: E402
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)

# Huge floor => no block ever qualifies => the per-pair loop always runs.
_VEC_OFF = "1000000000"
# Floor of 2 => every >=2 block takes the vectorized lane.
_VEC_ON = "2"


def _make_config(scorer: str, threshold: float) -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="last_name_fuzzy",
                type="weighted",
                fields=[MatchkeyField(field="last_name", scorer=scorer, weight=1.0)],
                threshold=threshold,
            ),
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["last_name"], transforms=["soundex"])],
        ),
    )


def _result_signature(res) -> tuple:
    """Order-independent fingerprint of a dedupe result for parity comparison.

    Uses scored_pairs (the emitted (id_a, id_b, score) set) for an exact pair-
    level check, plus the cluster/dupe counts as a structural backstop."""
    sp = res.scored_pairs
    if isinstance(sp, pl.DataFrame) and sp.height:
        cols = sp.columns
        a, b, s = cols[0], cols[1], cols[2]
        pairs = frozenset(
            (min(r[a], r[b]), max(r[a], r[b]), round(float(r[s]), 9))
            for r in sp.iter_rows(named=True)
        )
    else:
        pairs = frozenset()
    n_dupes = res.dupes.height if hasattr(res.dupes, "height") else len(res.dupes)
    return (res.total_clusters, n_dupes, len(pairs), hash(pairs))


def _run(df: pl.DataFrame, cfg: GoldenMatchConfig, vec_floor: str) -> tuple[float, object]:
    os.environ["GOLDENMATCH_BUCKET_VEC_MIN"] = vec_floor
    t0 = time.perf_counter()
    res = dedupe_df(df, config=cfg, backend="bucket")
    return time.perf_counter() - t0, res


def _median_wall(df, cfg, vec_floor, reps) -> float:
    _run(df, cfg, vec_floor)  # warm
    return statistics.median(_run(df, cfg, vec_floor)[0] for _ in range(reps))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--rows", type=int, default=20000)
    p.add_argument("--scorer", default="soundex_match")
    p.add_argument("--threshold", type=float, default=0.85)
    p.add_argument("--reps", type=int, default=5)
    p.add_argument("--sweep", action="store_true", help="block-size cross-over sweep")
    args = p.parse_args()

    cfg = _make_config(args.scorer, args.threshold)
    df = realistic_person_df(args.rows)
    print(f"rows={args.rows} scorer={args.scorer} threshold={args.threshold} reps={args.reps}")

    # Parity: forced-OFF vs forced-ON must produce an identical result.
    _, res_off = _run(df, cfg, _VEC_OFF)
    _, res_on = _run(df, cfg, _VEC_ON)
    sig_off, sig_on = _result_signature(res_off), _result_signature(res_on)
    parity = "OK" if sig_off == sig_on else f"MISMATCH off={sig_off} on={sig_on}"
    print(f"  parity (lane off vs on): {parity}")

    # Wall A/B.
    w_off = _median_wall(df, cfg, _VEC_OFF, args.reps)
    w_on = _median_wall(df, cfg, _VEC_ON, args.reps)
    speedup = w_off / w_on if w_on else float("nan")
    print(f"  per-pair loop : {w_off*1000:8.1f} ms")
    print(f"  vectorized    : {w_on*1000:8.1f} ms")
    print(f"  speedup       : {speedup:6.2f}x  (ship rule: >=2x end-to-end)")

    if args.sweep:
        print("  cross-over sweep (GOLDENMATCH_BUCKET_VEC_MIN):")
        for floor in ("2", "8", "16", "32", "64", "128"):
            w = _median_wall(df, cfg, floor, max(args.reps, 3))
            print(f"    vec_min={floor:>4}: {w*1000:8.1f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
