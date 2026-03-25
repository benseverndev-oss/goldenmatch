from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Static,
    TabbedContent,
    TabPane,
)


class ProfileTab(TabPane):
    """Column types, samples, and issues."""

    def __init__(self, df=None, profile=None):
        super().__init__("Profile", id="profile-tab")
        self._df = df
        self._profile = profile

    def compose(self) -> ComposeResult:
        if self._profile:
            table = DataTable()
            table.add_columns("Column", "Type", "Nulls", "Unique", "Sample")
            for col in self._profile.columns:
                table.add_row(
                    col.name,
                    col.inferred_type,
                    f"{col.null_count} ({col.null_pct:.0%})",
                    str(col.unique_count),
                    ", ".join(col.sample_values[:3]),
                )
            yield table
        else:
            yield Static("No data loaded. Use: goldenflow interactive <file>")


class TransformTab(TabPane):
    """Select columns, pick transforms, preview results."""

    def __init__(self):
        super().__init__("Transform", id="transform-tab")

    def compose(self) -> ComposeResult:
        yield Static("Select columns and transforms to apply.")


class MapTab(TabPane):
    """Schema mapping editor."""

    def __init__(self):
        super().__init__("Map", id="map-tab")

    def compose(self) -> ComposeResult:
        yield Static("Load source and target files to auto-map schemas.")


class ExportTab(TabPane):
    """Save cleaned data, config, and manifest."""

    def __init__(self):
        super().__init__("Export", id="export-tab")

    def compose(self) -> ComposeResult:
        yield Static("Export cleaned data, YAML config, and JSON manifest.")


class GoldenFlowApp(App):
    """GoldenFlow interactive TUI."""

    TITLE = "GoldenFlow"
    CSS = """
    TabbedContent { height: 100%; }
    """

    def __init__(self, path: Path | None = None):
        super().__init__()
        self._path = path
        self._df = None
        self._profile = None
        self.title = "GoldenFlow"

        if path and path.exists():
            from goldenflow.connectors.file import read_file
            from goldenflow.engine.profiler_bridge import profile_dataframe

            self._df = read_file(path)
            self._profile = profile_dataframe(self._df, file_path=str(path))

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            yield ProfileTab(df=self._df, profile=self._profile)
            yield TransformTab()
            yield MapTab()
            yield ExportTab()
        yield Footer()
