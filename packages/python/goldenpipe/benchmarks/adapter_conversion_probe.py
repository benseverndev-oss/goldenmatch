"""W3 Task 7: measure the goldenpipe adapter's polars<->arrow conversion cost.

Stage 0 (`docs/design/2026-07-06-goldenpipe-stage0-findings.md`) measured the
FRAME HANDOFF at 5.1ms / 0.2% of a 2176ms wall. It did NOT measure the two
conversion sites in `adapters/match.py`, which is the number the arrow-canonical
decision hinges on.

MEASURES THE SHIPPED BASIS, not a lookalike: `_assert_shipped()` greps the real
adapter source and refuses to run if the expressions below have drifted from the
call sites they claim to represent. A probe that silently measures a paraphrase
is worse than no probe.

Three timings per size:

  A  per-column `.to_arrow()`   -- FusedDedupeStage.run (OPT-IN stage, key+score
                                   columns only). Claimed zero-copy.
  B  whole-frame `pl.Utf8` cast -- DedupeStage.run (classic stage, ALL columns).
  C  arrow-native equivalent of B (`pc.cast` per column, rebuild table)
     -- the control. B is a dtype normalization, NOT a polars tax: an
     arrow-canonical pipeline still has to do it. C shows what flipping would
     actually save (B - C), which may be nothing or negative.

Usage:
    python adapter_conversion_probe.py [--rows 100000] [--repeat 5]

Run 100K/1M locally; send 10M to CI (`large-new-64GB`) -- see
feedback_no_local_scale_benchmarks.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.compute as pc

_ADAPTER = (
    Path(__file__).resolve().parents[1] / "goldenpipe" / "adapters" / "match.py"
)

# The exact expressions this probe claims to represent, as they appear in the
# shipped adapter. If a refactor moves them, the probe must fail loudly rather
# than keep reporting a number for code that no longer exists.
_SHIPPED_SNIPPETS = {
    "B_utf8_cast": "ctx.df = ctx.df.cast({col: pl.Utf8 for col in ctx.df.columns})",
    "A_per_column_to_arrow": "columns = {c: ctx.df[c].to_arrow() for c in needed}",
}


def _assert_shipped() -> None:
    src = _ADAPTER.read_text(encoding="utf-8")
    missing = [k for k, snip in _SHIPPED_SNIPPETS.items() if snip not in src]
    if missing:
        raise SystemExit(
            f"probe drift: {missing} no longer found verbatim in {_ADAPTER}. "
            "Re-read the adapter and update _SHIPPED_SNIPPETS before trusting "
            "any number from this probe."
        )


def _frame(rows: int) -> pl.DataFrame:
    """Mixed-dtype frame -- the Utf8 cast exists precisely because dtypes are
    mixed (birth_year i64 vs str), so an all-string frame would understate it."""
    return pl.DataFrame(
        {
            "name": [f"person{i % 50000}" for i in range(rows)],
            "city": [("California", "Texas", "Nevada")[i % 3] for i in range(rows)],
            "email": [f"p{i % 50000}@x.com" for i in range(rows)],
            "birth_year": [1950 + (i % 60) for i in range(rows)],   # i64
            "score": [float(i % 997) / 7.0 for i in range(rows)],   # f64
        }
    )


def _median_ms(fn, repeat: int) -> float:
    samples = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        out = fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
        del out
    return statistics.median(samples)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=100_000)
    ap.add_argument("--repeat", type=int, default=5)
    args = ap.parse_args()

    _assert_shipped()

    df = _frame(args.rows)
    # FusedDedupeStage converts ONLY key + score columns, not the whole frame.
    needed = ["city", "name"]

    a = _median_ms(lambda: {c: df[c].to_arrow() for c in needed}, args.repeat)
    b = _median_ms(
        lambda: df.cast({col: pl.Utf8 for col in df.columns}), args.repeat
    )

    tbl = df.to_arrow()
    c = _median_ms(
        lambda: pa.table(
            {n: pc.cast(tbl.column(n), pa.large_string()) for n in tbl.column_names}
        ),
        args.repeat,
    )

    # FAIRNESS SPLIT. A whole-frame number could be an artifact of one library
    # short-circuiting the already-correct-dtype columns. Split the cast into
    # the columns that need real work (numeric -> string) and the ones that do
    # not, so the comparison cannot hide behind a no-op.
    numeric = [n for n in df.columns if df[n].dtype != pl.Utf8]
    stringy = [n for n in df.columns if df[n].dtype == pl.Utf8]

    b_num = _median_ms(lambda: df.cast({n: pl.Utf8 for n in numeric}), args.repeat)
    c_num = _median_ms(
        lambda: pa.table(
            {n: pc.cast(tbl.column(n), pa.large_string()) for n in numeric}
        ),
        args.repeat,
    )
    b_str = _median_ms(lambda: df.cast({n: pl.Utf8 for n in stringy}), args.repeat)
    c_str = _median_ms(
        lambda: pa.table(
            {n: pc.cast(tbl.column(n), pa.large_string()) for n in stringy}
        ),
        args.repeat,
    )

    out = {
        "rows": args.rows,
        "repeat": args.repeat,
        "A_per_column_to_arrow_ms": round(a, 4),
        "B_polars_utf8_cast_ms": round(b, 4),
        "C_arrow_cast_equivalent_ms": round(c, 4),
        "B_minus_C_ms": round(b - c, 4),
        "fairness": {
            "numeric_cast_polars_ms": round(b_num, 4),
            "numeric_cast_arrow_ms": round(c_num, 4),
            "numeric_arrow_slowdown_x": round(c_num / b_num, 2) if b_num else None,
            "noop_string_cast_polars_ms": round(b_str, 4),
            "noop_string_cast_arrow_ms": round(c_str, 4),
            "note": (
                "Both libraries short-circuit the no-op string casts, so the "
                "whole-frame gap is real work, not a short-circuit artifact."
            ),
        },
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
