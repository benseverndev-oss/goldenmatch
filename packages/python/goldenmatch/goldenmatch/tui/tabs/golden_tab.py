"""Golden tab — golden record preview with confidence scores."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static

from goldenmatch.tui.engine import EngineResult


class GoldenTab(Static):
    """Golden record preview with per-field confidence and color coding."""

    DEFAULT_CSS = """
    GoldenTab {
        height: 1fr;
    }
    #golden-table {
        height: 1fr;
        border: solid $primary;
    }
    """

    BINDINGS = [
        # Phase 4 follow-up (#437 surface sync, 2026-05-22): open the
        # GoldenEditModal on the currently highlighted DataTable row to
        # file a field-level Correction. `e` chosen over Enter to avoid
        # clashing with DataTable's row-selection default.
        Binding("e", "edit_golden_field", "Edit field"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._result: EngineResult | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(
                "[dim]Run matching to see golden records here.[/dim]",
                id="golden-placeholder",
            )
            yield DataTable(id="golden-table")

    def on_mount(self) -> None:
        table = self.query_one("#golden-table", DataTable)
        table.display = False

    def update_results(self, result: EngineResult) -> None:
        """Populate table with golden records from the engine result."""
        self._result = result
        placeholder = self.query_one("#golden-placeholder", Static)
        table = self.query_one("#golden-table", DataTable)

        golden_df = result.golden
        if golden_df is None or golden_df.height == 0:
            placeholder.update("[dim]Run matching to see golden records here.[/dim]")
            placeholder.display = True
            table.display = False
            return

        # Hide placeholder, show table
        placeholder.display = False
        table.clear(columns=True)
        table.display = True

        # Determine data columns (exclude internal columns)
        data_cols = [
            c for c in golden_df.columns
            if c not in ("__cluster_id__", "__golden_confidence__")
        ]

        # Add columns: Cluster ID, Confidence, then data columns
        table.add_column("Cluster ID")
        table.add_column("Confidence")
        for col in data_cols:
            table.add_column(col)

        # Add rows
        for row in golden_df.iter_rows(named=True):
            cluster_id = str(row.get("__cluster_id__", ""))
            confidence = row.get("__golden_confidence__", 0.0)
            if confidence is None:
                confidence = 0.0

            # Format confidence with color coding
            conf_str = f"{confidence:.2f}"
            if confidence > 0.9:
                conf_str = f"[green]{conf_str}[/green]"
            elif confidence >= 0.7:
                conf_str = f"[yellow]{conf_str}[/yellow]"
            else:
                conf_str = f"[red]{conf_str}[/red]"

            values = [str(row.get(c, "")) for c in data_cols]
            table.add_row(cluster_id, conf_str, *values)

    def action_edit_golden_field(self) -> None:
        """Open GoldenEditModal for the highlighted cell.

        Reads `cluster_id` from the row's first column (always present)
        and `field_name` + `original_value` from the cursor column. The
        first two columns (Cluster ID, Confidence) are non-editable; the
        action exits silently when the cursor is on either.
        """
        from goldenmatch.tui.screens.golden_edit_modal import GoldenEditModal

        try:
            table = self.query_one("#golden-table", DataTable)
        except Exception:
            return
        if not table.display or table.row_count == 0:
            return
        row_idx = table.cursor_row
        col_idx = table.cursor_column
        if row_idx is None or col_idx is None:
            return
        # Cursor on Cluster ID (0) or Confidence (1) -- no editable target.
        if col_idx < 2:
            return
        try:
            row = table.get_row_at(row_idx)
        except Exception:
            return
        try:
            cluster_id = int(str(row[0]))
        except (ValueError, TypeError):
            return

        # Resolve column name + original value from the same row.
        columns = list(table.columns.values())
        if col_idx >= len(columns):
            return
        field_name = str(columns[col_idx].label)
        original_value = str(row[col_idx])

        dataset = (
            getattr(self.app, "memory_dataset", None)
            or getattr(self.app, "current_dataset", None)
            or "tui"
        )
        modal = GoldenEditModal(
            cluster_id=cluster_id,
            field_name=field_name,
            original_value=original_value,
            dataset=dataset,
        )
        self.app.push_screen(modal)
