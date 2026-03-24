"""Jupyter notebook display hooks for GoldenCheck results."""
from __future__ import annotations

from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import DatasetProfile, ColumnProfile

__all__ = ["ScanResult", "findings_to_html", "profile_to_html"]


# --- Finding display ---

_SEVERITY_COLORS = {
    Severity.ERROR: "#ff4444",
    Severity.WARNING: "#ffbb33",
    Severity.INFO: "#33b5e5",
}

_SEVERITY_LABELS = {
    Severity.ERROR: "ERROR",
    Severity.WARNING: "WARNING",
    Severity.INFO: "INFO",
}


def _finding_to_html(f: Finding) -> str:
    color = _SEVERITY_COLORS.get(f.severity, "#888")
    label = _SEVERITY_LABELS.get(f.severity, "?")
    conf = f"{'H' if f.confidence >= 0.8 else 'M' if f.confidence >= 0.5 else 'L'}"
    source = " [LLM]" if f.source == "llm" else ""
    samples = ", ".join(f.sample_values[:3]) if f.sample_values else ""
    return (
        f'<tr>'
        f'<td style="color:{color};font-weight:bold">{label}</td>'
        f'<td>{f.column}</td>'
        f'<td>{f.check}</td>'
        f'<td>{f.message}</td>'
        f'<td style="text-align:right">{f.affected_rows}</td>'
        f'<td>{conf}{source}</td>'
        f'<td style="color:#888;font-size:0.85em">{samples}</td>'
        f'</tr>'
    )


def findings_to_html(findings: list[Finding]) -> str:
    header = (
        '<table style="border-collapse:collapse;width:100%;font-family:monospace;font-size:13px">'
        '<thead><tr style="border-bottom:2px solid #444;text-align:left">'
        '<th>Severity</th><th>Column</th><th>Check</th>'
        '<th>Message</th><th>Rows</th><th>Conf</th><th>Samples</th>'
        '</tr></thead><tbody>'
    )
    rows = "".join(_finding_to_html(f) for f in findings)
    return header + rows + "</tbody></table>"


# --- Profile display ---

def _health_badge(grade: str, score: int) -> str:
    colors = {"A": "#00ff00", "B": "#7fff00", "C": "#ffff00", "D": "#ff7f00", "F": "#ff0000"}
    color = colors.get(grade, "#888")
    return (
        f'<span style="background:{color};color:#000;padding:2px 8px;'
        f'border-radius:4px;font-weight:bold;font-size:1.1em">'
        f'{grade} ({score})</span>'
    )


def _col_profile_row(col: ColumnProfile) -> str:
    top = ", ".join(f"{v}({c})" for v, c in col.top_values[:3]) if col.top_values else ""
    return (
        f'<tr>'
        f'<td style="font-weight:bold">{col.name}</td>'
        f'<td>{col.inferred_type}</td>'
        f'<td style="text-align:right">{col.null_pct:.1f}%</td>'
        f'<td style="text-align:right">{col.unique_pct:.1f}%</td>'
        f'<td style="color:#888;font-size:0.85em">{top}</td>'
        f'</tr>'
    )


def profile_to_html(profile: DatasetProfile, findings: list[Finding] | None = None) -> str:
    if findings:
        by_col: dict[str, dict[str, int]] = {}
        for f in findings:
            if f.severity >= Severity.WARNING:
                by_col.setdefault(f.column, {"errors": 0, "warnings": 0})
                key = "errors" if f.severity == Severity.ERROR else "warnings"
                by_col[f.column][key] = by_col[f.column].get(key, 0) + 1
        grade, score = profile.health_score(findings_by_column=by_col)
    else:
        grade, score = profile.health_score()

    badge = _health_badge(grade, score)
    header = (
        f'<div style="font-family:monospace;font-size:13px">'
        f'<div style="margin-bottom:8px">'
        f'<strong>{profile.file_path}</strong> &mdash; '
        f'{profile.row_count:,} rows, {profile.column_count} columns '
        f'&mdash; Health: {badge}</div>'
        f'<table style="border-collapse:collapse;width:100%">'
        f'<thead><tr style="border-bottom:2px solid #444;text-align:left">'
        f'<th>Column</th><th>Type</th><th>Null%</th><th>Unique%</th><th>Top Values</th>'
        f'</tr></thead><tbody>'
    )
    rows = "".join(_col_profile_row(c) for c in profile.columns)
    return header + rows + "</tbody></table></div>"


# --- ScanResult wrapper ---

class ScanResult:
    """Wrapper for scan results with rich Jupyter display."""

    def __init__(
        self,
        findings: list[Finding],
        profile: DatasetProfile,
    ):
        self.findings = findings
        self.profile = profile

    def _repr_html_(self) -> str:
        parts = [
            '<div style="font-family:monospace">',
            '<h3 style="color:#FFD700;margin:0 0 12px 0">GoldenCheck Results</h3>',
            profile_to_html(self.profile, self.findings),
            '<div style="margin-top:16px">',
            f'<strong>{len(self.findings)} findings</strong>',
            '</div>',
            findings_to_html(self.findings),
            '</div>',
        ]
        return "\n".join(parts)

    def __repr__(self) -> str:
        errors = sum(1 for f in self.findings if f.severity == Severity.ERROR)
        warnings = sum(1 for f in self.findings if f.severity == Severity.WARNING)
        return (
            f"ScanResult({len(self.findings)} findings: "
            f"{errors} errors, {warnings} warnings)"
        )
