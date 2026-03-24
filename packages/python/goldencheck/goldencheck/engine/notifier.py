"""Webhook notifier — POST scan results to external URLs."""
from __future__ import annotations

import json
import logging
import urllib.request

from goldencheck.models.finding import Finding, Severity
from goldencheck.engine.history import ScanRecord

logger = logging.getLogger(__name__)

__all__ = ["send_webhook", "should_notify"]


def should_notify(
    current_grade: str,
    current_findings: list[Finding],
    previous_scan: ScanRecord | None,
    notify_on: str,
) -> bool:
    """Determine whether to fire a webhook notification."""
    errors = sum(1 for f in current_findings if f.severity == Severity.ERROR)
    warnings = sum(1 for f in current_findings if f.severity == Severity.WARNING)

    if notify_on == "any-error":
        return errors > 0
    elif notify_on == "any-warning":
        return errors > 0 or warnings > 0
    elif notify_on == "grade-drop":
        if previous_scan is None:
            return False
        grade_order = "ABCDF"
        old_idx = grade_order.index(previous_scan.grade) if previous_scan.grade in grade_order else 0
        new_idx = grade_order.index(current_grade) if current_grade in grade_order else 0
        return new_idx > old_idx  # higher index = worse grade

    return False


def send_webhook(
    url: str,
    file: str,
    grade: str,
    score: int,
    findings: list[Finding],
    trigger: str,
    previous_grade: str | None = None,
) -> None:
    """POST scan results to a webhook URL. Fire-and-forget."""
    from goldencheck import __version__

    errors = sum(1 for f in findings if f.severity == Severity.ERROR)
    warnings = sum(1 for f in findings if f.severity == Severity.WARNING)

    top = []
    for f in findings[:10]:
        if f.severity >= Severity.WARNING:
            top.append({
                "severity": f.severity.name.lower(),
                "column": f.column,
                "check": f.check,
                "message": f.message[:200],
            })

    payload = {
        "tool": "goldencheck",
        "version": __version__,
        "trigger": trigger,
        "file": file,
        "health_grade": grade,
        "health_score": score,
        "previous_grade": previous_grade,
        "errors": errors,
        "warnings": warnings,
        "top_findings": top,
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
        logger.info("Webhook sent to %s", url)
    except Exception as e:
        logger.warning("Webhook failed (%s): %s", url, e)
