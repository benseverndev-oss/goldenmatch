"""JSON reporter — machine-readable output matching spec schema."""
from __future__ import annotations
import json
from typing import IO
from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import DatasetProfile


def report_json(findings: list[Finding], profile: DatasetProfile, output: IO) -> None:
    errors = sum(1 for f in findings if f.severity == Severity.ERROR)
    warnings = sum(1 for f in findings if f.severity == Severity.WARNING)
    infos = len(findings) - errors - warnings
    grade, points = profile.health_score(errors=errors, warnings=warnings)
    data = {
        "file": profile.file_path,
        "rows": profile.row_count,
        "columns": profile.column_count,
        "health_score": {"grade": grade, "points": points},
        "summary": {"errors": errors, "warnings": warnings, "info": infos},
        "findings": [
            {
                k: v for k, v in {
                    "severity": f.severity.name.lower(),
                    "column": f.column,
                    "check": f.check,
                    "message": f.message,
                    "affected_rows": f.affected_rows,
                    "sample_values": f.sample_values,
                    "source": f.source,
                }.items() if v is not None
            }
            for f in findings
        ],
    }
    json.dump(data, output, indent=2)
    output.write("\n")
