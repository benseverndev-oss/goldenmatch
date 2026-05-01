"""Rules tab — pinned rules from findings + existing config rules."""
from __future__ import annotations
from textual.widgets import Static, DataTable
from textual.containers import Vertical
from goldencheck.models.finding import Finding
from goldencheck.config.schema import GoldenCheckConfig


class RulesPane(Vertical):
    def __init__(self, findings: list[Finding], config: GoldenCheckConfig, **kwargs):
        super().__init__(**kwargs)
        self.findings = findings
        self.config = config

    def compose(self):
        yield Static("[bold #FFD700]Pinned Rules[/bold #FFD700]  |  Press F2 to save to goldencheck.yml\n")
        table = DataTable(id="rules-table")
        table.add_columns("Column", "Check", "Source")
        # Existing config rules
        for col_name, rule in self.config.columns.items():
            table.add_row(col_name, f"type={rule.type}", "config")
        yield table
        yield Static("\n[dim]Pin findings in the Findings tab to add rules here.[/dim]")
