"""
GoldenFlow Speed Benchmark
Measures throughput (rows/sec) and peak memory for increasing dataset sizes.
Applies a fixed config: strip + phone_e164 + date_iso8601 + state_abbreviate
"""
from __future__ import annotations

import gc
import random
import sys
import time
import tracemalloc
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import goldenflow  # noqa: F401 – registers transforms
from goldenflow.engine.transformer import TransformEngine
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

SIZES = [1_000, 10_000, 100_000, 1_000_000]
SEED = 42

PHONE_POOL = [
    "(555) 123-4567", "555.987.6543", "+1-555-456-7890",
    "5551234567", "555-CALL-NOW",
]
DATE_POOL = [
    "03/15/2024", "2024-01-20", "Jan 5, 2023", "March 15, 2024", "invalid-date",
]
STATE_POOL = [
    "Pennsylvania", "TX", "new york", "CA", "Colorado", "Florida",
]


def build_df(n: int, seed: int = SEED) -> pl.DataFrame:
    rng = random.Random(seed)
    phones = [rng.choice(PHONE_POOL) for _ in range(n)]
    dates = [rng.choice(DATE_POOL) for _ in range(n)]
    states = [rng.choice(STATE_POOL) for _ in range(n)]
    names = [f"  user_{i}  " for i in range(n)]
    return pl.DataFrame({
        "full_name":   names,
        "phone":       phones,
        "signup_date": dates,
        "state":       states,
    })


def make_config() -> GoldenFlowConfig:
    return GoldenFlowConfig(
        transforms=[
            TransformSpec(column="full_name",   ops=["strip"]),
            TransformSpec(column="phone",        ops=["phone_e164"]),
            TransformSpec(column="signup_date",  ops=["date_iso8601"]),
            TransformSpec(column="state",        ops=["state_abbreviate"]),
        ]
    )


def benchmark_size(n: int) -> dict:
    df = build_df(n)
    config = make_config()
    engine = TransformEngine(config=config)

    gc.collect()
    tracemalloc.start()
    t0 = time.perf_counter()

    result = engine.transform_df(df)

    elapsed = time.perf_counter() - t0
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    throughput = n / elapsed if elapsed > 0 else float("inf")
    peak_mb = peak_bytes / (1024 * 1024)

    return {
        "n": n,
        "elapsed_s": round(elapsed, 4),
        "throughput": int(throughput),
        "peak_mb": round(peak_mb, 2),
        "rows_transformed": result.df.shape[0],
    }


def print_speed_table(results: list[dict]) -> None:
    if HAS_RICH:
        console = Console()
        table = Table(
            title="GoldenFlow Speed Benchmark (strip + phone_e164 + date_iso8601 + state_abbreviate)",
            box=box.ROUNDED,
        )
        table.add_column("Dataset Size", justify="right", style="cyan")
        table.add_column("Elapsed (s)", justify="right")
        table.add_column("Throughput (rows/s)", justify="right", style="green")
        table.add_column("Peak Memory (MB)", justify="right")
        for r in results:
            table.add_row(
                f"{r['n']:,}",
                f"{r['elapsed_s']:.4f}",
                f"{r['throughput']:,}",
                f"{r['peak_mb']:.2f}",
            )
        console.print(table)
    else:
        print("\n=== Speed Benchmark ===")
        print(f"{'Size':>12} {'Elapsed(s)':>12} {'Rows/sec':>14} {'PeakMB':>10}")
        print("-" * 55)
        for r in results:
            print(
                f"{r['n']:>12,} {r['elapsed_s']:>12.4f} "
                f"{r['throughput']:>14,} {r['peak_mb']:>10.2f}"
            )


def main() -> None:
    print("GoldenFlow Speed Benchmark")
    print("Config: strip + phone_e164 + date_iso8601 + state_abbreviate\n")

    results = []
    for n in SIZES:
        print(f"  Benchmarking {n:,} rows…", end=" ", flush=True)
        r = benchmark_size(n)
        print(f"{r['throughput']:,} rows/s  ({r['elapsed_s']:.3f}s, {r['peak_mb']:.1f} MB peak)")
        results.append(r)

    print()
    print_speed_table(results)
    print("\nSpeed benchmark complete.")


if __name__ == "__main__":
    main()
