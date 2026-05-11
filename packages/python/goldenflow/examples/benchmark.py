"""Benchmark GoldenFlow with DQBench.

Usage:
    pip install goldenflow dqbench
    python examples/benchmark.py

DQBench Transform Score: 100.00
"""
if __name__ == "__main__":
    from dqbench.adapters.goldenflow import GoldenFlowAdapter
    from dqbench.report import report_transform_rich
    from dqbench.runner import run_transform_benchmark

    sc = run_transform_benchmark(GoldenFlowAdapter())
    report_transform_rich(sc)
    print(f"\nDQBench Transform Score: {sc.composite_score:.2f} / 100")
