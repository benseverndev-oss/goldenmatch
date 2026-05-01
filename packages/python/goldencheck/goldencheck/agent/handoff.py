"""Pipeline handoff module — generates a structured handoff dict for downstream consumers."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from goldencheck import __version__
from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import DatasetProfile

__all__ = ["generate_handoff", "findings_to_fbc"]


def findings_to_fbc(findings: list[Finding]) -> dict[str, dict[str, int]]:
    """Convert a list of findings to a findings-by-column dict for health_score.

    Returns ``{col_name: {"errors": N, "warnings": N}}`` with counts aggregated
    per column.
    """
    fbc: dict[str, dict[str, int]] = defaultdict(lambda: {"errors": 0, "warnings": 0})
    for f in findings:
        if f.severity == Severity.ERROR:
            fbc[f.column]["errors"] += 1
        elif f.severity == Severity.WARNING:
            fbc[f.column]["warnings"] += 1
    return dict(fbc)


def _compute_file_hash(file_path: str) -> str:
    """Return ``sha256:<hex>`` digest of the file at *file_path*."""
    h = hashlib.sha256()
    with Path(file_path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _attestation(
    findings: list[Finding],
    review_pending: int,
    dismissed: int,  # noqa: ARG001 — kept for signature symmetry
) -> str:
    """Derive the attestation label from findings and review state."""
    has_errors = any(f.severity == Severity.ERROR for f in findings)
    has_warnings = any(f.severity == Severity.WARNING for f in findings)

    if has_errors:
        return "FAIL"
    if review_pending > 0:
        return "REVIEW_REQUIRED"
    if has_warnings:
        return "PASS_WITH_WARNINGS"
    return "PASS"


def _build_columns(
    profile: DatasetProfile,
    findings: list[Finding],
    column_types: dict[str, str] | None,
) -> dict[str, dict]:
    """Build the per-column summary dict."""
    # Group findings by column
    issues_by_col: dict[str, list[dict]] = defaultdict(list)
    for f in findings:
        issues_by_col[f.column].append({
            "check": f.check,
            "severity": f.severity.name,
            "message": f.message,
            "confidence": f.confidence,
            "affected_rows": f.affected_rows,
        })

    columns: dict[str, dict] = {}
    for cp in profile.columns:
        col_type = (column_types or {}).get(cp.name, cp.inferred_type)
        columns[cp.name] = {
            "type": col_type,
            "null_pct": round(cp.null_pct, 4),
            "unique_pct": round(cp.unique_pct, 4),
            "issues": issues_by_col.get(cp.name, []),
        }
    return columns


def generate_handoff(
    file_path: str,
    findings: list[Finding],
    profile: DatasetProfile,
    pinned_rules: list[dict],
    review_pending: int,
    dismissed: int,
    job_name: str,
    column_types: dict[str, str] | None = None,
) -> dict:
    """Generate a structured handoff dict summarising a GoldenCheck scan.

    The returned dict is JSON-serialisable and follows ``schema_version: 1``.
    """
    # Counts
    error_count = sum(1 for f in findings if f.severity == Severity.ERROR)
    warning_count = sum(1 for f in findings if f.severity == Severity.WARNING)

    # Health score via profile helper
    fbc = findings_to_fbc(findings)
    grade, score = profile.health_score(findings_by_column=fbc)

    # Unresolved: medium-confidence findings (not yet pinned or dismissed)
    unresolved = [
        asdict(f)
        for f in findings
        if 0.5 <= f.confidence < 0.8
    ]

    return {
        "schema_version": 1,
        "source_tool": "goldencheck",
        "source_version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "job_name": job_name,
        "file_path": file_path,
        "file_hash": _compute_file_hash(file_path),
        "row_count": profile.row_count,
        "column_count": profile.column_count,
        "health": {"grade": grade, "score": score},
        "summary": {
            "errors": error_count,
            "warnings": warning_count,
            "pinned_rules": len(pinned_rules),
            "review_pending": review_pending,
            "dismissed": dismissed,
        },
        "columns": _build_columns(profile, findings, column_types),
        "pinned_rules": pinned_rules,
        "unresolved_findings": unresolved,
        "attestation": _attestation(findings, review_pending, dismissed),
    }
