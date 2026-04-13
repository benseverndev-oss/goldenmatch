#!/usr/bin/env python3
"""Generate golden outputs for TypeScript parity tests.

Run from repo root:
    python scripts/gen_parity_goldens_js.py
"""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from goldencheck.engine.scanner import scan_file
from goldencheck.engine.confidence import apply_confidence_downgrade
from goldencheck.models.finding import Severity

FIXTURES_DIR = Path("tests/fixtures")
GOLDENS_DIR = FIXTURES_DIR / "_goldens_js"
MANIFEST_PATH = FIXTURES_DIR / "parity_cases.json"


def main() -> None:
    if not MANIFEST_PATH.exists():
        print(f"No manifest at {MANIFEST_PATH} — creating sample manifest")
        create_sample_manifest()

    manifest = json.loads(MANIFEST_PATH.read_text())

    GOLDENS_DIR.mkdir(parents=True, exist_ok=True)

    for case in manifest["cases"]:
        name = case["name"]
        records = case["input"]["records"]
        options = case.get("options", {})

        # Write records to temp CSV
        df = pl.DataFrame(records)
        tmp_csv = GOLDENS_DIR / f"_tmp_{name}.csv"
        df.write_csv(tmp_csv)

        try:
            findings, profile = scan_file(
                tmp_csv,
                sample_size=options.get("sampleSize", 100000),
                domain=options.get("domain"),
            )
            findings = apply_confidence_downgrade(findings, llm_boost=False)

            # Build golden output
            golden = {
                "findings": [
                    {
                        "severity": f.severity.name,
                        "column": f.column,
                        "check": f.check,
                        "confidence": round(f.confidence, 4),
                    }
                    for f in findings
                ],
                "health_grade": profile.health_score(
                    findings_by_column=_findings_by_column(findings)
                )[0],
                "health_score": profile.health_score(
                    findings_by_column=_findings_by_column(findings)
                )[1],
            }

            out_path = GOLDENS_DIR / f"{name}.json"
            out_path.write_text(json.dumps(golden, indent=2))
            print(f"  ✓ {name} → {len(findings)} findings")

        finally:
            tmp_csv.unlink(missing_ok=True)

    print(f"\nGenerated {len(manifest['cases'])} golden(s) in {GOLDENS_DIR}")


def _findings_by_column(findings):
    by_col = {}
    for f in findings:
        if f.severity >= Severity.WARNING:
            by_col.setdefault(f.column, {"errors": 0, "warnings": 0})
            key = "errors" if f.severity == Severity.ERROR else "warnings"
            by_col[f.column][key] = by_col[f.column].get(key, 0) + 1
    return by_col


def create_sample_manifest() -> None:
    """Create a minimal parity test manifest."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    cases = [
        {
            "name": "simple_mixed",
            "description": "Basic mixed-type data with common quality issues",
            "input": {
                "kind": "records",
                "records": [
                    {"id": i, "name": f"Person_{i}", "email": f"user{i}@test.com" if i < 18 else "bad",
                     "age": 20 + i if i < 19 else -5, "status": ["active", "inactive", "pending"][i % 3]}
                    for i in range(20)
                ],
            },
            "options": {"sampleSize": 100000, "domain": None},
        },
    ]

    manifest = {"cases": cases}
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    print(f"Created sample manifest at {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
