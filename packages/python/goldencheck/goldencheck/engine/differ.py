"""Diff engine — compare two versions of a data file."""
from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl

from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import DatasetProfile


@dataclass
class SchemaChange:
    change_type: str  # "added", "removed", "type_changed"
    column: str
    old_type: str | None = None
    new_type: str | None = None


@dataclass
class FindingChange:
    change_type: str  # "new", "resolved", "worsened", "improved"
    column: str
    check: str
    old_severity: str | None = None
    new_severity: str | None = None
    old_rows: int = 0
    new_rows: int = 0
    message: str = ""


@dataclass
class StatChange:
    metric: str
    old_value: str
    new_value: str
    delta: str


@dataclass
class DiffReport:
    schema_changes: list[SchemaChange] = field(default_factory=list)
    finding_changes: list[FindingChange] = field(default_factory=list)
    stat_changes: list[StatChange] = field(default_factory=list)


def diff_files(
    old_df: pl.DataFrame,
    new_df: pl.DataFrame,
    old_findings: list[Finding],
    new_findings: list[Finding],
    old_profile: DatasetProfile | None = None,
    new_profile: DatasetProfile | None = None,
) -> DiffReport:
    """Compare two versions of a dataset and return a diff report."""
    report = DiffReport()

    # --- Schema changes ---
    old_cols = set(old_df.columns)
    new_cols = set(new_df.columns)

    for col in sorted(new_cols - old_cols):
        report.schema_changes.append(SchemaChange(
            change_type="added", column=col,
            new_type=str(new_df[col].dtype),
        ))

    for col in sorted(old_cols - new_cols):
        report.schema_changes.append(SchemaChange(
            change_type="removed", column=col,
            old_type=str(old_df[col].dtype),
        ))

    for col in sorted(old_cols & new_cols):
        old_type = str(old_df[col].dtype)
        new_type = str(new_df[col].dtype)
        if old_type != new_type:
            report.schema_changes.append(SchemaChange(
                change_type="type_changed", column=col,
                old_type=old_type, new_type=new_type,
            ))

    # --- Finding changes ---
    old_by_key = {}
    for f in old_findings:
        if f.severity >= Severity.WARNING:
            key = (f.column, f.check)
            if key not in old_by_key or f.severity > old_by_key[key].severity:
                old_by_key[key] = f

    new_by_key = {}
    for f in new_findings:
        if f.severity >= Severity.WARNING:
            key = (f.column, f.check)
            if key not in new_by_key or f.severity > new_by_key[key].severity:
                new_by_key[key] = f

    all_keys = set(old_by_key.keys()) | set(new_by_key.keys())
    for key in sorted(all_keys):
        old_f = old_by_key.get(key)
        new_f = new_by_key.get(key)

        if new_f and not old_f:
            report.finding_changes.append(FindingChange(
                change_type="new", column=key[0], check=key[1],
                new_severity=new_f.severity.name,
                new_rows=new_f.affected_rows,
                message=new_f.message,
            ))
        elif old_f and not new_f:
            report.finding_changes.append(FindingChange(
                change_type="resolved", column=key[0], check=key[1],
                old_severity=old_f.severity.name,
                old_rows=old_f.affected_rows,
                message=old_f.message,
            ))
        elif old_f and new_f:
            if new_f.severity > old_f.severity or new_f.affected_rows > old_f.affected_rows * 1.5:
                report.finding_changes.append(FindingChange(
                    change_type="worsened", column=key[0], check=key[1],
                    old_severity=old_f.severity.name, new_severity=new_f.severity.name,
                    old_rows=old_f.affected_rows, new_rows=new_f.affected_rows,
                    message=new_f.message,
                ))
            elif new_f.severity < old_f.severity or new_f.affected_rows < old_f.affected_rows * 0.5:
                report.finding_changes.append(FindingChange(
                    change_type="improved", column=key[0], check=key[1],
                    old_severity=old_f.severity.name, new_severity=new_f.severity.name,
                    old_rows=old_f.affected_rows, new_rows=new_f.affected_rows,
                    message=new_f.message,
                ))

    # --- Stat changes ---
    old_rows = len(old_df)
    new_rows = len(new_df)
    if old_rows != new_rows:
        pct = ((new_rows - old_rows) / old_rows * 100) if old_rows > 0 else 0
        report.stat_changes.append(StatChange(
            metric="Rows",
            old_value=f"{old_rows:,}",
            new_value=f"{new_rows:,}",
            delta=f"{pct:+.0f}%",
        ))

    if len(old_df.columns) != len(new_df.columns):
        diff = len(new_df.columns) - len(old_df.columns)
        report.stat_changes.append(StatChange(
            metric="Columns",
            old_value=str(len(old_df.columns)),
            new_value=str(len(new_df.columns)),
            delta=f"{diff:+d}",
        ))

    return report


def format_diff_report(report: DiffReport, label: str = "") -> str:
    """Format a DiffReport as human-readable text."""
    lines = []
    if label:
        lines.append(f"goldencheck diff — {label}")
        lines.append("")

    if report.schema_changes:
        lines.append("Schema changes:")
        for c in report.schema_changes:
            if c.change_type == "added":
                lines.append(f"  + {c.column} ({c.new_type})")
            elif c.change_type == "removed":
                lines.append(f"  - {c.column} ({c.old_type})")
            elif c.change_type == "type_changed":
                lines.append(f"  ~ {c.column}: {c.old_type} -> {c.new_type}")
        lines.append("")

    if report.finding_changes:
        lines.append("Finding changes:")
        for c in report.finding_changes:
            if c.change_type == "new":
                lines.append(f"  NEW   [{c.column}] {c.message[:60]}")
            elif c.change_type == "resolved":
                lines.append(f"  FIXED [{c.column}] {c.check} resolved")
            elif c.change_type == "worsened":
                lines.append(f"  WORSE [{c.column}] {c.old_rows} -> {c.new_rows} rows")
            elif c.change_type == "improved":
                lines.append(f"  BETTER [{c.column}] {c.old_rows} -> {c.new_rows} rows")
        lines.append("")

    if report.stat_changes:
        lines.append("Stats:")
        for c in report.stat_changes:
            lines.append(f"  {c.metric}: {c.old_value} -> {c.new_value} ({c.delta})")
        lines.append("")

    if not report.schema_changes and not report.finding_changes and not report.stat_changes:
        lines.append("No changes detected.")

    return "\n".join(lines)
