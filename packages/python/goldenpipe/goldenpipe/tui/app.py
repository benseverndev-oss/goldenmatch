"""GoldenPipe TUI -- 4-tab pipeline interface."""
from __future__ import annotations

try:
    from textual.app import App, ComposeResult
    from textual.widgets import Header, Footer, Static, TabbedContent, TabPane
    HAS_TEXTUAL = True
except ImportError:
    HAS_TEXTUAL = False


if HAS_TEXTUAL:
    class GoldenPipeApp(App):
        """GoldenPipe interactive TUI."""

        TITLE = "GoldenPipe"

        BINDINGS = [
            ("q", "quit", "Quit"),
            ("1", "tab_pipeline", "Pipeline"),
            ("2", "tab_config", "Config"),
            ("3", "tab_results", "Results"),
            ("4", "tab_log", "Log"),
        ]

        def compose(self) -> ComposeResult:
            yield Header()
            with TabbedContent():
                with TabPane("Pipeline", id="tab-pipeline"):
                    yield Static("Pipeline stages will appear here.\nPress 'r' to run.")
                with TabPane("Config", id="tab-config"):
                    yield Static("YAML config editor.\nLoad a config file to edit.")
                with TabPane("Results", id="tab-results"):
                    yield Static("Artifacts browser.\nRun a pipeline to see results.")
                with TabPane("Log", id="tab-log"):
                    yield Static("Reasoning and timing log.\nRun a pipeline to see logs.")
            yield Footer()

        def action_tab_pipeline(self) -> None:
            self.query_one(TabbedContent).active = "tab-pipeline"

        def action_tab_config(self) -> None:
            self.query_one(TabbedContent).active = "tab-config"

        def action_tab_results(self) -> None:
            self.query_one(TabbedContent).active = "tab-results"

        def action_tab_log(self) -> None:
            self.query_one(TabbedContent).active = "tab-log"
