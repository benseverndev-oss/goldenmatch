"""golden-diagnostics -- anomaly diagnostics + prefilled GitHub issue prompts.

Turns a genuine anomaly (a wheel-skew slow path, an unexpected crash, a
broken optional-dependency install) into an actionable message with a
prefilled 'file an issue' URL. Sends nothing anywhere -- it is a better error
message, not telemetry -- so it is safe on by default. Fires only on
anomalies, never on expected fallbacks or user-input errors.
"""
from __future__ import annotations

from golden_diagnostics.report import (
    environment_report,
    is_expected,
    issue_url,
    prompts_enabled,
    report_anomaly,
    report_exception,
    reset,
)

__all__ = [
    "environment_report",
    "is_expected",
    "issue_url",
    "prompts_enabled",
    "report_anomaly",
    "report_exception",
    "reset",
]

__version__ = "0.1.0"
