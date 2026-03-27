"""Run a full check -> flow -> match pipeline on a CSV file.

GoldenPipe orchestrates GoldenCheck (validate), GoldenFlow (transform),
and GoldenMatch (deduplicate) in a single pipeline run.

Usage:
    python basic_pipeline.py data.csv
"""
import sys

from goldenpipe import run, PipeStatus


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else "data.csv"
    result = run(source)

    print(f"Pipeline status: {result.status.name}")
    print(f"Input rows: {result.input_rows}")
    print(f"Stages run: {len(result.stage_results)}\n")

    for sr in result.stage_results:
        print(f"  [{sr.status.name}] {sr.stage} -- {sr.summary}")

    if result.status == PipeStatus.FAILED:
        print(f"\nErrors: {result.errors}")
        sys.exit(1)

    if result.df is not None:
        print(f"\nOutput: {result.df.shape[0]} rows x {result.df.shape[1]} cols")


if __name__ == "__main__":
    main()
