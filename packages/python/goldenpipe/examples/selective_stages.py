"""Run only the check and flow stages, skipping match.

Use PipelineConfig to control which stages execute. This is useful when
you want validation + transformation but not deduplication.

Usage:
    python selective_stages.py data.csv
"""
import sys

import polars as pl

from goldenpipe import run_df, PipelineConfig, StageSpec, PipeStatus


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else "data.csv"
    df = pl.read_csv(source, ignore_errors=True)

    config = PipelineConfig(
        pipeline="check-and-flow-only",
        stages=[
            StageSpec(use="check"),
            StageSpec(use="flow"),
        ],
    )

    result = run_df(df, config=config)

    print(f"Pipeline status: {result.status.name}")
    for sr in result.stage_results:
        print(f"  [{sr.status.name}] {sr.stage} -- {sr.summary}")

    if result.df is not None:
        print(f"\nOutput: {result.df.shape[0]} rows x {result.df.shape[1]} cols")


if __name__ == "__main__":
    main()
