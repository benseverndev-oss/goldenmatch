"""02 — full Suite: GoldenCheck → GoldenFlow → GoldenMatch, composed manually.

Demonstrates each stage's API rather than wrapping it via GoldenPipe. Use when
you need to inspect or branch on intermediate results — for example, to fail
on critical findings before running expensive transforms or matching.

Run:
    pip install goldencheck goldenflow goldenmatch polars
    python 02_full_suite_pipeline.py customers.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

import goldencheck
import goldenflow
import goldenmatch
import polars as pl


def main(path: str) -> None:
    csv = Path(path)
    df = pl.read_csv(csv, encoding="utf8-lossy", ignore_errors=True)
    print(f"loaded {df.height} rows × {df.width} cols")

    # 1. GoldenCheck — surface quality issues
    scan = goldencheck.scan_file(str(csv))
    findings = scan.to_dict().get("findings", [])
    print(f"GoldenCheck: {len(findings)} findings")
    critical = [f for f in findings if f.get("severity") == "critical"]
    if critical:
        print(f"  ⚠ {len(critical)} critical — bailing out")
        for f in critical[:5]:
            print(f"    {f.get('check')}: {f.get('column')} ({f.get('rate', 0):.1%})")
        raise SystemExit(2)

    # 2. GoldenFlow — standardize messy fields. Use findings to drive transforms.
    if findings:
        from goldenflow.engine.selector import select_from_findings
        from goldenflow.config.schema import GoldenFlowConfig, TransformSpec

        ops = select_from_findings(findings)
        config = GoldenFlowConfig(transforms=[
            TransformSpec(column=op["column"], ops=[op["transform"]]) for op in ops
        ])
        cleaned = goldenflow.transform_df(df, config=config)
    else:
        cleaned = goldenflow.transform_df(df)
    print(f"GoldenFlow: applied {len(cleaned.manifest.records)} transforms")

    # 3. GoldenMatch — cluster + golden records
    result = goldenmatch.dedupe_df(
        cleaned.df,
        exact=["email"],
        fuzzy={"first_name": 0.85, "last_name": 0.85},
        blocking=["zip"],
        threshold=0.85,
    )
    print(f"GoldenMatch: {result.total_clusters} clusters, "
          f"{result.match_rate:.1%} match rate")

    # 4. Persist
    if result.golden is not None:
        out = csv.with_name(csv.stem + ".golden.parquet")
        result.golden.write_parquet(out)
        print(f"wrote golden records → {out}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python 02_full_suite_pipeline.py path/to/customers.csv")
    main(sys.argv[1])
