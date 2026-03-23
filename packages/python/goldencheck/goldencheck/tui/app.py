"""Main TUI application for GoldenCheck."""
from __future__ import annotations
from pathlib import Path
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Header, Footer, TabbedContent, TabPane
from goldencheck.models.finding import Finding
from goldencheck.models.profile import DatasetProfile
from goldencheck.config.schema import GoldenCheckConfig, ColumnRule
from goldencheck.config.writer import save_config
from goldencheck.tui.overview import OverviewPane
from goldencheck.tui.findings import FindingsPane
from goldencheck.tui.column_detail import ColumnDetailPane
from goldencheck.tui.rules import RulesPane


class GoldenCheckApp(App):
    CSS = """
    Screen { background: $surface; }
    TabbedContent { height: 100%; }
    #overview { padding: 1 2; }
    .gold { color: #FFD700; }
    .health-a { color: #00ff00; }
    .health-b { color: #7fff00; }
    .health-c { color: #ffff00; }
    .health-d { color: #ff7f00; }
    .health-f { color: #ff0000; }
    .severity-error { color: red; }
    .severity-warning { color: yellow; }
    .severity-info { color: cyan; }
    """

    BINDINGS = [
        Binding("1", "switch_tab('overview')", "Overview", show=True),
        Binding("2", "switch_tab('findings')", "Findings", show=True),
        Binding("3", "switch_tab('column-detail')", "Column Detail", show=True),
        Binding("4", "switch_tab('rules')", "Rules", show=True),
        Binding("f2", "save_rules", "Save Rules"),
        Binding("q", "quit", "Quit"),
        Binding("question_mark", "show_help", "Help"),
    ]

    def __init__(self, findings: list[Finding], profile: DatasetProfile,
                 config: GoldenCheckConfig | None = None, **kwargs):
        super().__init__(**kwargs)
        self.findings = findings
        self.profile = profile
        self.config = config or GoldenCheckConfig()
        self.title = "GoldenCheck"

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            with TabPane("Overview", id="overview"):
                yield OverviewPane(self.findings, self.profile)
            with TabPane("Findings", id="findings"):
                yield FindingsPane(self.findings)
            with TabPane("Column Detail", id="column-detail"):
                yield ColumnDetailPane(self.profile)
            with TabPane("Rules", id="rules"):
                yield RulesPane(self.findings, self.config)
        yield Footer()

    def action_switch_tab(self, tab_id: str) -> None:
        self.query_one(TabbedContent).active = tab_id

    def action_save_rules(self) -> None:
        # Build config from pinned findings
        for f in self.findings:
            if f.pinned and f.column not in self.config.columns:
                self.config.columns[f.column] = ColumnRule(type="string")
        save_config(self.config, Path("goldencheck.yml"))
        self.notify("Rules saved to goldencheck.yml")

    def action_show_help(self) -> None:
        self.notify("1-4: Switch tabs | Space: Pin rule | F2: Save | e: View rows | q: Quit")
