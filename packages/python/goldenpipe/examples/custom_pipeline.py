"""Build a custom pipeline with specific stages and config.

Shows how to use PipelineConfig to control stage order, skip stages,
and pass stage-specific configuration.

Usage:
    pip install goldenpipe[golden-suite]
    python examples/custom_pipeline.py
"""
from __future__ import annotations
import csv
import tempfile
from pathlib import Path

import polars as pl
from goldenpipe import Pipeline, PipelineConfig, StageSpec


def main():
    # Create sample data
    rows = [
        ["name", "email", "phone", "amount"],
        ["  John Smith  ", "JOHN@TEST.COM", "(555) 123-4567", "$1,234.56"],
        ["john smith", "john@test.com", "5551234567", "1234.56"],
        ["Jane Doe", "jane@test.com", "555-999-8888", "$999.99"],
    ]
    path = Path(tempfile.mktemp(suffix=".csv"))
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)

    # Option 1: Zero-config (runs all available stages)
    print("── Zero-Config Pipeline ──")
    pipeline = Pipeline()
    result = pipeline.run(source=str(path))
    print(f"Status: {result.status.name}")
    print(f"Stages: {list(result.stages.keys())}")

    # Option 2: Custom config (only check + flow, skip match)
    print("\n── Check + Flow Only ──")
    config = PipelineConfig(
        pipeline="validate-and-clean",
        stages=[
            StageSpec(use="goldencheck.scan"),
            StageSpec(use="goldenflow.transform"),
        ],
    )
    pipeline = Pipeline(config=config)
    result = pipeline.run(source=str(path))
    print(f"Status: {result.status.name}")
    print(f"Stages: {list(result.stages.keys())}")

    path.unlink()
    print("\nDone!")


if __name__ == "__main__":
    main()
