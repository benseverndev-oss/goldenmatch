"""Findings tab — DataTable with pin toggle."""
from __future__ import annotations
from textual.widgets import DataTable
from textual.containers import Vertical
from goldencheck.models.finding import Finding, Severity


class FindingsPane(Vertical):
    def __init__(self, findings: list[Finding], **kwargs):
        super().__init__(**kwargs)
        self.findings = findings

    def compose(self):
        table = DataTable(id="findings-table")
        table.add_columns("", "Severity", "Column", "Check", "Message", "Rows")
        for i, f in enumerate(self.findings):
            pin = "[x]" if f.pinned else "[ ]"
            sev = f.severity.name
            table.add_row(pin, sev, f.column, f.check, f.message[:60], str(f.affected_rows), key=str(i))
        yield table

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # Toggle pin on Space
        idx = int(event.row_key.value)
        if idx < len(self.findings):
            self.findings[idx].pinned = not self.findings[idx].pinned
            table = self.query_one("#findings-table", DataTable)
            pin = "[x]" if self.findings[idx].pinned else "[ ]"
            table.update_cell_at((idx, 0), pin)
