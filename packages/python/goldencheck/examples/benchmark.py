"""Benchmark GoldenCheck with DQBench.

Usage:
    pip install goldencheck dqbench
    python examples/benchmark.py

DQBench Detect Score: 88.40
"""
if __name__ == "__main__":
    from dqbench.adapters.goldencheck import GoldenCheckAdapter
    from dqbench.runner import run_benchmark
    from dqbench.report import report_rich

    sc = run_benchmark(GoldenCheckAdapter())
    report_rich(sc)
    print(f"\nDQBench Detect Score: {sc.dqbench_score:.2f} / 100")
