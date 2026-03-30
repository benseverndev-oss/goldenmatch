"""Benchmark the Golden Suite with DQBench.

Runs all 4 benchmark categories and prints a summary scorecard.

Usage:
    pip install goldenpipe[golden-suite] dqbench
    python examples/benchmark_suite.py

For best ER results, set OPENAI_API_KEY for LLM scoring (~$0.25).
"""
from __future__ import annotations
import time
import os


def main():
    from dqbench.runner import (
        run_benchmark,
        run_transform_benchmark,
        run_er_benchmark,
        run_pipeline_benchmark,
    )
    from dqbench.report import (
        report_rich,
        report_transform_rich,
        report_er_rich,
        report_pipeline_rich,
    )

    results = {}
    total_start = time.perf_counter()
    has_llm = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))

    # GoldenCheck (Detect)
    print("\n" + "=" * 60)
    print("GoldenCheck — DQBench Detect")
    print("=" * 60)
    from dqbench.adapters.goldencheck import GoldenCheckAdapter
    sc = run_benchmark(GoldenCheckAdapter())
    report_rich(sc)
    results["Detect"] = sc.dqbench_score

    # GoldenFlow (Transform)
    print("\n" + "=" * 60)
    print("GoldenFlow — DQBench Transform")
    print("=" * 60)
    from dqbench.adapters.goldenflow import GoldenFlowAdapter
    sc = run_transform_benchmark(GoldenFlowAdapter())
    report_transform_rich(sc)
    results["Transform"] = sc.composite_score

    # GoldenMatch (ER)
    print("\n" + "=" * 60)
    print("GoldenMatch — DQBench ER" + (" (with LLM)" if has_llm else ""))
    print("=" * 60)
    from dqbench.adapters.goldenmatch_adapter import GoldenMatchAdapter
    sc = run_er_benchmark(GoldenMatchAdapter())
    report_er_rich(sc)
    results["ER"] = sc.dqbench_er_score

    # GoldenPipe (Pipeline)
    print("\n" + "=" * 60)
    print("GoldenPipe — DQBench Pipeline")
    print("=" * 60)
    from dqbench.adapters.goldenpipe_adapter import GoldenPipeAdapter
    sc = run_pipeline_benchmark(GoldenPipeAdapter())
    report_pipeline_rich(sc)
    results["Pipeline"] = sc.dqbench_pipeline_score

    total_time = time.perf_counter() - total_start

    # Summary
    print("\n" + "=" * 60)
    print("Golden Suite — DQBench Scorecard")
    print("=" * 60)
    for category, score in results.items():
        bar = "█" * int(score / 2) + "░" * (50 - int(score / 2))
        print(f"  {category:12s} {bar} {score:6.2f}")
    print(f"\n  Total time: {total_time:.1f}s")
    if has_llm:
        print("  LLM scoring: enabled (OPENAI_API_KEY set)")
    else:
        print("  LLM scoring: disabled (set OPENAI_API_KEY for higher ER scores)")


if __name__ == "__main__":
    main()
