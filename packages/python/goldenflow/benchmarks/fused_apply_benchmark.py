"""GoldenFlow fused-apply A/B benchmark (Pillar-1 default-on gate).

Measures the fused columnar apply path (``GOLDENFLOW_FUSED_APPLY=1``) vs the
per-transform path over a realistic messy frame, at scale. This is the
measurement that decides whether the flag flips default-on: the in-Rust component
bench (``benches/chain_apply.rs``) showed 1.36x, but the dominant win is the
per-transform path crossing the Python/Polars/Arrow boundary N times (+ rebuilding
the column + a full-column affected scan) that the fused path pays once — visible
only end-to-end, here.

Each variant runs in its OWN subprocess so ``ru_maxrss`` (peak RSS) is clean per
variant (it's a monotonic high-water mark; two variants in one process would
contaminate it). Parity at scale is checked by comparing a digest of each
variant's output frame — the fused path MUST be byte-identical to per-transform.

Usage:
    python benchmarks/fused_apply_benchmark.py --rows 1000000
    python benchmarks/fused_apply_benchmark.py --rows 1000000 --variant fused  # one leg (JSON)

The fused leg only measures a real win when the native kernel is built (the
``apply_chain_arrow`` symbol); otherwise it silently equals the per-transform path
and the report says so.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import polars as pl

try:
    import resource  # Unix only; CI is Linux (KB units)
except ImportError:  # pragma: no cover - Windows local runs
    resource = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import goldenflow  # noqa: E402,F401 -- registers transforms
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec  # noqa: E402
from goldenflow.engine.transformer import TransformEngine  # noqa: E402

SEED = 42

# Realistic mess: the kinds of noise these owned kernels fix.
NAME_POOL = [
    "  John  SMITH!  ",
    "o'BRIEN, jr.",
    "MARY-JANE  ",
    "  van der BERG ",
    "de la CRUZ #3",
    "  D'Angelo, Sr.  ",
]
BIO_POOL = [
    "<b>Hi</b> visit http://x.com/y now",
    "plain bio, nothing fancy",
    "  <i>note</i>   more   text  ",
    "see https://a.co?q=1&utm_source=z end",
    "<p>hello</p>  world",
]
EMAIL_POOL = [
    "  John.SMITH@Googlemail.com ",
    "o'brien+tag@Example.CO ",
    " MARY_JANE@work.org",
    "DE.LA.CRUZ@Company.com  ",
    " d'angelo@Foo.Bar ",
]

# Per-column chains of ONLY fusable (owned, string->string, no-arg) ops — the case
# the fused path targets, and now spanning the WIDENED set: text + NAME normalizers
# + EMAIL family (these last two previously fell to the per-transform path). 13 ops
# across 3 columns: per-transform crosses the boundary 13 times; fused, 3.
CONFIG = GoldenFlowConfig(
    transforms=[
        TransformSpec(
            column="full_name",
            ops=["strip", "name_transliterate", "name_proper", "strip_titles", "strip_suffixes"],
        ),
        TransformSpec(
            column="email",
            ops=["strip", "lowercase", "email_normalize", "email_canonical"],
        ),
        TransformSpec(
            column="bio",
            ops=["remove_html_tags", "remove_urls", "strip", "collapse_whitespace"],
        ),
    ]
)


def build_df(rows: int, seed: int = SEED) -> pl.DataFrame:
    rng = random.Random(seed)
    return pl.DataFrame(
        {
            "full_name": [f"{rng.choice(NAME_POOL)}{i % 97}" for i in range(rows)],
            "email": [rng.choice(EMAIL_POOL) for _ in range(rows)],
            "bio": [f"{rng.choice(BIO_POOL)} #{i % 89}" for i in range(rows)],
        }
    )


def _digest(df: pl.DataFrame) -> str:
    """Stable cross-process digest of the output frame (for the parity check)."""
    return hashlib.sha256(df.write_csv().encode("utf-8")).hexdigest()[:16]


def _peak_rss_mb() -> float:
    if resource is None:
        return 0.0
    # Linux ru_maxrss is KB; macOS is bytes — CI is Linux.
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def run_variant(variant: str, rows: int, runs: int, seed: int) -> dict:
    """Run one leg (per_transform | fused): warmup + `runs` timed passes, returning
    the median wall, throughput, peak RSS, output digest, and whether the fused
    native path actually engaged."""
    if variant == "fused":
        os.environ["GOLDENFLOW_FUSED_APPLY"] = "1"
    else:
        os.environ.pop("GOLDENFLOW_FUSED_APPLY", None)

    from goldenflow.transforms._chain import fused_enabled

    df = build_df(rows, seed)
    engine = TransformEngine(config=CONFIG)

    result = engine.transform_df(df)  # warmup (also the digest source)
    times = []
    for _ in range(runs):
        gc.collect()
        t0 = time.perf_counter()
        engine.transform_df(df)
        times.append((time.perf_counter() - t0) * 1000.0)
    times.sort()
    wall = times[len(times) // 2]

    return {
        "variant": variant,
        "rows": rows,
        "wall_ms": round(wall, 1),
        "throughput": int(rows / (wall / 1000.0)) if wall else 0,
        "rss_mb": _peak_rss_mb(),
        "records": len(result.manifest.records),
        "digest": _digest(result.df),
        "fused_engaged": variant == "fused" and fused_enabled(),
    }


def _run_leg_subprocess(variant: str, rows: int, runs: int, seed: int) -> dict:
    proc = subprocess.run(
        [
            sys.executable,
            __file__,
            "--variant",
            variant,
            "--rows",
            str(rows),
            "--runs",
            str(runs),
            "--seed",
            str(seed),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "POLARS_SKIP_CPU_CHECK": "1", "PYTHONIOENCODING": "utf-8"},
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + proc.stderr)
        raise SystemExit(f"{variant} leg failed (exit {proc.returncode})")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--variant", choices=["both", "per_transform", "fused"], default="both")
    args = ap.parse_args()

    if args.variant != "both":
        # Single leg: emit JSON (consumed by the orchestrator subprocess).
        print(json.dumps(run_variant(args.variant, args.rows, args.runs, args.seed)))
        return

    base = _run_leg_subprocess("per_transform", args.rows, args.runs, args.seed)
    fused = _run_leg_subprocess("fused", args.rows, args.runs, args.seed)

    parity = base["digest"] == fused["digest"]
    speedup = base["wall_ms"] / fused["wall_ms"] if fused["wall_ms"] else float("nan")
    rss_delta = fused["rss_mb"] - base["rss_mb"]

    n_ops = sum(len(t.ops) for t in CONFIG.transforms)
    n_cols = len(CONFIG.transforms)
    print(f"GoldenFlow fused-apply A/B -- {args.rows:,} rows, {args.runs}-run median wall")
    print(f"config: {n_ops} fusable ops across {n_cols} columns ({base['records']} audit records)\n")
    print(f"  {'variant':<16}{'wall (ms)':>12}{'M rows/s':>12}{'peak RSS MB':>14}")
    for r in (base, fused):
        print(
            f"  {r['variant']:<16}{r['wall_ms']:>12.1f}"
            f"{r['throughput'] / 1e6:>12.2f}{r['rss_mb']:>14.1f}"
        )
    print()
    print(f"  speedup (wall):  {speedup:.2f}x")
    print(f"  RSS delta:       {rss_delta:+.1f} MB")
    print(f"  parity (digest): {'OK -- byte-identical output' if parity else 'FAIL -- DIVERGED'}")
    if not fused["fused_engaged"]:
        print(
            "\n  NOTE: the fused native kernel did NOT engage (apply_chain_arrow "
            "missing / GOLDENFLOW_NATIVE=0) -- the 'fused' leg == per-transform, so\n"
            "  the speedup above is NOT a real measurement. Build native-flow first."
        )
    if not parity:
        raise SystemExit("parity FAILED: fused output diverged from per-transform")


if __name__ == "__main__":
    main()
