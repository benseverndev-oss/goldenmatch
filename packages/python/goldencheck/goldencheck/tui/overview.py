"""Overview tab — health score and dataset stats."""
from __future__ import annotations
from textual.widgets import Static
from textual.containers import Vertical
from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import DatasetProfile


class OverviewPane(Vertical):
    def __init__(self, findings: list[Finding], profile: DatasetProfile, **kwargs):
        super().__init__(**kwargs)
        self.findings = findings
        self.profile = profile

    def compose(self):
        errors = sum(1 for f in self.findings if f.severity == Severity.ERROR)
        warnings = sum(1 for f in self.findings if f.severity == Severity.WARNING)
        infos = len(self.findings) - errors - warnings
        grade, points = self.profile.health_score(errors=errors, warnings=warnings)
        color_class = f"health-{grade.lower()}"

        yield Static(f"[bold #FFD700]GoldenCheck[/bold #FFD700] — {self.profile.file_path}\n")
        yield Static(f"Health Score: [{color_class}][bold]{grade}[/bold] ({points}/100)[/{color_class}]")
        yield Static(f"Rows: {self.profile.row_count:,}  |  Columns: {self.profile.column_count}")
        yield Static(f"\n[red]{errors} errors[/red]  |  [yellow]{warnings} warnings[/yellow]  |  [cyan]{infos} info[/cyan]")
        yield Static("\nColumns profiled:")
        for cp in self.profile.columns:
            null_str = f"  nulls: {cp.null_count}" if cp.null_count > 0 else ""
            yield Static(f"  {cp.name} ({cp.inferred_type}){null_str}")
