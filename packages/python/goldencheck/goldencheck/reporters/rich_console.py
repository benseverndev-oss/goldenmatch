"""Rich console reporter — prints findings as formatted table."""
from __future__ import annotations
from rich.console import Console
from rich.table import Table
from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import DatasetProfile

SEVERITY_COLORS = {Severity.ERROR: "red", Severity.WARNING: "yellow", Severity.INFO: "cyan"}


def report_rich(findings: list[Finding], profile: DatasetProfile) -> None:
    console = Console()
    # Header
    errors = sum(1 for f in findings if f.severity == Severity.ERROR)
    warnings = sum(1 for f in findings if f.severity == Severity.WARNING)
    grade, points = profile.health_score(errors=errors, warnings=warnings)
    console.print(f"\n[bold]GoldenCheck Report[/bold] — {profile.file_path}")
    console.print(f"Rows: {profile.row_count:,}  Columns: {profile.column_count}  Health: [bold]{grade}[/bold] ({points})")
    console.print(f"Findings: [red]{errors} errors[/red], [yellow]{warnings} warnings[/yellow], {len(findings) - errors - warnings} info\n")
    if not findings:
        console.print("[green]No issues found![/green]")
        return
    table = Table(show_header=True)
    table.add_column("Severity", width=8)
    table.add_column("Column", width=20)
    table.add_column("Check", width=15)
    table.add_column("Message")
    table.add_column("Conf", width=4)
    for f in findings:
        color = SEVERITY_COLORS.get(f.severity, "white")
        conf = "H" if f.confidence >= 0.8 else "M" if f.confidence >= 0.5 else "[red]L[/red]"
        table.add_row(f"[{color}]{f.severity.name}[/{color}]", f.column, f.check, f.message, conf)
    console.print(table)
