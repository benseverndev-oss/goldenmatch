"""Golden Suite Full Demo — see each tool in action, then the pipeline.

Shows GoldenCheck, GoldenFlow, GoldenMatch, and GoldenPipe working
individually and together on the same messy dataset.

Usage:
    pip install goldenpipe[golden-suite]
    python examples/full_suite_demo.py
"""
from __future__ import annotations
import csv
import tempfile
from pathlib import Path

import polars as pl


def create_sample_data() -> Path:
    """Create a messy CSV with quality issues and duplicates."""
    rows = [
        ["first_name", "last_name", "email", "phone", "city", "state"],
        ["  John  ", "Smith", "JOHN@ACME.COM", "(555) 123-4567", "new york", "NY"],
        ["john", "smith", "john@acme.com", "5551234567", "New York", "NY"],
        ["Jane", "Doe", "jane@corp.com", "555-987-6543", "Chicago", "IL"],
        ["  JANE", "DOE", "JANE@CORP.COM", "(555)987-6543", "  chicago  ", "IL"],
        ["Bob", "Wilson", "bob@test.com", "555 111 2222", "Boston", "MA"],
        ["Alice", "  Brown  ", "ALICE@WORK.ORG", "(555) 333-4444", "austin", "TX"],
        ["alice", "brown", "alice@work.org", "555.333.4444", "Austin", "TX"],
        ["Charlie", "Davis", "charlie@mail.com", "(555) 555-5555", "Seattle", "WA"],
    ]
    path = Path(tempfile.mktemp(suffix=".csv"))
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)
    return path


def demo_goldencheck(csv_path: Path):
    """Step 1: Scan for quality issues."""
    print("\n" + "=" * 60)
    print("Step 1: GoldenCheck — Scan for Quality Issues")
    print("=" * 60)
    import goldencheck
    findings = goldencheck.scan_file(str(csv_path))
    print(f"\nFound {len(findings)} issues:")
    for f in findings[:5]:
        print(f"  [{f.severity}] {f.column}: {f.message}")
    if len(findings) > 5:
        print(f"  ... and {len(findings) - 5} more")
    score = goldencheck.health_score(str(csv_path))
    print(f"\nHealth Score: {score}")


def demo_goldenflow(csv_path: Path) -> pl.DataFrame:
    """Step 2: Transform and fix issues."""
    print("\n" + "=" * 60)
    print("Step 2: GoldenFlow — Transform & Standardize")
    print("=" * 60)
    import goldenflow
    from goldenflow.config.schema import GoldenFlowConfig, TransformSpec

    df = pl.read_csv(csv_path)
    config = GoldenFlowConfig(transforms=[
        TransformSpec(column="first_name", ops=["strip", "title_case"]),
        TransformSpec(column="last_name", ops=["strip", "title_case"]),
        TransformSpec(column="email", ops=["strip", "lowercase"]),
        TransformSpec(column="phone", ops=["strip", "phone_national"]),
        TransformSpec(column="city", ops=["strip", "title_case"]),
    ])
    result = goldenflow.transform_df(df, config=config)
    print(f"\nTransformed {result.manifest.total_transforms} cells")
    print(f"Before: {df.shape[0]} rows")
    print(f"After:  {result.df.shape[0]} rows")

    # Show before/after for first record
    print(f"\nSample transform:")
    for col in ["first_name", "email", "phone", "city"]:
        if col in df.columns:
            print(f"  {col}: \"{df[col][0]}\" → \"{result.df[col][0]}\"")
    return result.df


def demo_goldenmatch(cleaned_df: pl.DataFrame) -> pl.DataFrame:
    """Step 3: Deduplicate records."""
    print("\n" + "=" * 60)
    print("Step 3: GoldenMatch — Deduplicate & Match")
    print("=" * 60)
    import goldenmatch

    result = goldenmatch.dedupe_df(
        cleaned_df,
        fuzzy={"first_name": 0.8, "last_name": 0.8},
        exact=["email"],
    )
    print(f"\nRecords: {result.total_records}")
    print(f"Clusters: {result.total_clusters}")
    print(f"Match rate: {result.match_rate:.1%}")
    print(f"Unique: {result.unique.shape[0] if result.unique is not None else 0}")
    print(f"Golden: {result.golden.shape[0] if result.golden is not None else 0}")

    output = result.unique if result.unique is not None else cleaned_df
    if result.golden is not None and result.golden.shape[0] > 0:
        # Simplified merge
        cols = [c for c in output.columns if c in result.golden.columns]
        output = pl.concat([output.select(cols), result.golden.select(cols)], how="diagonal")
    return output


def demo_goldenpipe(csv_path: Path):
    """Step 4: Run the full pipeline in one command."""
    print("\n" + "=" * 60)
    print("Step 4: GoldenPipe — Full Pipeline (One Command)")
    print("=" * 60)
    from goldenpipe import Pipeline

    pipeline = Pipeline()
    result = pipeline.run(source=str(csv_path))
    print(f"\nPipeline status: {result.status.name}")
    print(f"Input rows: {result.input_rows}")
    print(f"Stages: {len(result.stages)}")
    for name, stage in result.stages.items():
        print(f"  [{stage.status.name}] {name}")
    if result.reasoning:
        print("\nReasoning:")
        for stage, reason in result.reasoning.items():
            print(f"  {stage}: {reason}")


def main():
    print("Golden Suite Full Demo")
    print("=" * 60)

    csv_path = create_sample_data()
    print(f"\nSample data: {csv_path}")
    df = pl.read_csv(csv_path)
    print(f"Rows: {df.shape[0]}, Columns: {df.shape[1]}")
    print(df)

    # Run each tool individually
    demo_goldencheck(csv_path)
    cleaned = demo_goldenflow(csv_path)
    deduped = demo_goldenmatch(cleaned)

    print("\n" + "=" * 60)
    print("Result: Cleaned & Deduplicated")
    print("=" * 60)
    print(f"\n{df.shape[0]} messy rows → {deduped.shape[0]} clean, unique records")
    print(deduped)

    # Then show the pipeline doing it all at once
    demo_goldenpipe(csv_path)

    csv_path.unlink()
    print("\nDone!")


if __name__ == "__main__":
    main()
