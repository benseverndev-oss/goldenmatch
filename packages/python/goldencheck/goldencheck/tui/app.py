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
        Binding("g", "guided_review", "Guided"),
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

    def action_guided_review(self) -> None:
        """Walk through findings one at a time with pin/dismiss."""
        from goldencheck.models.finding import Severity as _Sev
        reviewable = [f for f in self.findings if f.severity >= _Sev.WARNING and not f.pinned]
        if not reviewable:
            self.notify("No findings to review (all pinned or INFO-only)")
            return

        self._guided_findings = reviewable
        self._guided_index = 0
        self._show_guided_finding()

    def _show_guided_finding(self) -> None:
        if self._guided_index >= len(self._guided_findings):
            pinned_count = sum(1 for f in self.findings if f.pinned)
            self.notify(f"Guided review complete. {pinned_count} rules pinned. Press F2 to save.")
            return

        f = self._guided_findings[self._guided_index]
        conf = "HIGH" if f.confidence >= 0.8 else "MED" if f.confidence >= 0.5 else "LOW"
        total = len(self._guided_findings)
        idx = self._guided_index + 1
        self.notify(
            f"[{idx}/{total}] {f.severity.name} [{f.column}] {f.message[:60]} "
            f"(Conf: {conf}) — Space=Pin, n=Skip, Esc=Stop",
            timeout=0,
        )

    def action_show_help(self) -> None:
        self.notify("1-4: Switch tabs | Space: Pin rule | F2: Save | g: Guided | e: View rows | q: Quit")
