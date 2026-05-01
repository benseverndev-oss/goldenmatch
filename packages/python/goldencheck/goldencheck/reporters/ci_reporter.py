"""CI reporter — determines exit code based on findings and fail_on threshold."""
from __future__ import annotations
from goldencheck.models.finding import Finding, Severity

SEVERITY_MAP = {"error": Severity.ERROR, "warning": Severity.WARNING, "info": Severity.INFO}


def report_ci(findings: list[Finding], fail_on: str = "error") -> int:
    threshold = SEVERITY_MAP.get(fail_on, Severity.ERROR)
    for f in findings:
        if f.severity >= threshold:
            return 1
    return 0
