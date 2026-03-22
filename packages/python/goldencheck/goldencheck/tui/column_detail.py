"""Column Detail tab — per-column profile information."""
from __future__ import annotations
from textual.widgets import Static, OptionList
from textual.containers import Vertical
from goldencheck.models.profile import DatasetProfile


class ColumnDetailPane(Vertical):
    def __init__(self, profile: DatasetProfile, **kwargs):
        super().__init__(**kwargs)
        self.profile = profile

    def compose(self):
        yield Static("[bold]Select a column:[/bold]")
        options = OptionList(*[cp.name for cp in self.profile.columns], id="column-list")
        yield options
        yield Static("", id="column-info")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        idx = event.option_index
        if idx < len(self.profile.columns):
            cp = self.profile.columns[idx]
            info = (
                f"[bold]{cp.name}[/bold]\n"
                f"Type: {cp.inferred_type}\n"
                f"Rows: {cp.row_count:,}\n"
                f"Nulls: {cp.null_count} ({cp.null_pct:.1%})\n"
                f"Unique: {cp.unique_count} ({cp.unique_pct:.1%})\n"
            )
            if cp.min_value:
                info += f"Min: {cp.min_value}\n"
            if cp.max_value:
                info += f"Max: {cp.max_value}\n"
            if cp.mean is not None:
                info += f"Mean: {cp.mean:.2f}\n"
            if cp.detected_format:
                info += f"Format: {cp.detected_format}\n"
            if cp.enum_values:
                info += f"Enum: {', '.join(cp.enum_values)}\n"
            self.query_one("#column-info", Static).update(info)
