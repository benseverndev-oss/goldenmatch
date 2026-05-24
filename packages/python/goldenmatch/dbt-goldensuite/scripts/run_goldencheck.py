"""Run GoldenCheck against a dbt model's output.

Usage:
    python scripts/run_goldencheck.py <model_name> [--fail-on error] [--sample-size 100000]

This script:
1. Connects to the warehouse via dbt's profiles.yml
2. Queries the model table (with LIMIT for sampling)
3. Writes to a temp CSV
4. Runs goldencheck scan on the temp CSV
5. Returns exit code based on findings
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Run GoldenCheck on a dbt model")
    parser.add_argument("model", help="dbt model name (or table reference)")
    parser.add_argument("--fail-on", default="error", choices=["error", "warning"])
    parser.add_argument("--sample-size", type=int, default=100000)
    parser.add_argument("--domain", default=None)
    args = parser.parse_args()

    # Try to use dbt show to get data
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as tmp:
        tmp_path = tmp.name

    try:
        # Use dbt show to get model output as CSV
        cmd = [
            "dbt", "show",
            "--select", args.model,
            "--limit", str(args.sample_size),
            "--output", "json",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"dbt show failed: {result.stderr}", file=sys.stderr)
            print("Falling back to direct goldencheck scan...", file=sys.stderr)
            sys.exit(1)

        # Parse dbt show output and write to CSV
        try:
            import polars as pl
            data = json.loads(result.stdout)
            if isinstance(data, list) and data:
                df = pl.DataFrame(data)
                df.write_csv(tmp_path)
            else:
                print("No data returned from dbt show", file=sys.stderr)
                sys.exit(1)
        except (json.JSONDecodeError, ImportError) as e:
            print(f"Failed to parse dbt output: {e}", file=sys.stderr)
            sys.exit(1)

        # Run goldencheck scan
        gc_cmd = [
            "goldencheck", "scan", tmp_path,
            "--no-tui", "--json", "--fail-on", args.fail_on,
        ]
        if args.domain:
            gc_cmd.extend(["--domain", args.domain])

        gc_result = subprocess.run(gc_cmd, capture_output=True, text=True)

        # Print findings
        if gc_result.stdout:
            try:
                findings = json.loads(gc_result.stdout)
                errors = sum(
                    1 for f in findings.get("findings", [])
                    if f.get("severity", "").lower() == "error"
                )
                warnings = sum(
                    1 for f in findings.get("findings", [])
                    if f.get("severity", "").lower() == "warning"
                )
                print(f"GoldenCheck: {errors} errors, {warnings} warnings on model '{args.model}'")
            except json.JSONDecodeError:
                print(gc_result.stdout)

        sys.exit(gc_result.returncode)

    finally:
        Path(tmp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
