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
        Binding("d", "dismiss_finding", "Dismiss"),
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

    _dismissed: set = set()

    def action_dismiss_finding(self) -> None:
        """Dismiss the selected finding — adds to ignore list on F2 save."""
        # Find the currently selected finding in the findings pane
        findings_pane = self.query_one(FindingsPane)
        try:
            row_idx = findings_pane.table.cursor_row
            if row_idx is not None and 0 <= row_idx < len(self.findings):
                f = self.findings[row_idx]
                self._dismissed.add((f.column, f.check))
                self.notify(f"Dismissed: [{f.column}] {f.check} (saved on F2)")
        except Exception:
            self.notify("Select a finding first")

    def action_save_rules(self) -> None:
        # Build config from pinned findings
        for f in self.findings:
            if f.pinned and f.column not in self.config.columns:
                self.config.columns[f.column] = ColumnRule(type="string")
        # Add dismissed findings to ignore list
        from goldencheck.config.schema import IgnoreEntry
        for col, check in self._dismissed:
            entry = IgnoreEntry(column=col, check=check)
            if entry not in self.config.ignore:
                self.config.ignore.append(entry)
        save_config(self.config, Path("goldencheck.yml"))
        dismissed_count = len(self._dismissed)
        pinned_count = sum(1 for f in self.findings if f.pinned)
        self.notify(f"Saved: {pinned_count} rules pinned, {dismissed_count} dismissed")

    _guided_findings: list = []
    _guided_index: int = 0
    _guided_active: bool = False

    def action_guided_review(self) -> None:
        """Walk through findings one at a time with pin/dismiss."""
        from goldencheck.models.finding import Severity as _Sev
        reviewable = [f for f in self.findings if f.severity >= _Sev.WARNING and not f.pinned]
        if not reviewable:
            self.notify("No findings to review (all pinned or INFO-only)")
            return

        self._guided_findings = reviewable
        self._guided_index = 0
        self._guided_active = True
        self._show_guided_finding()

    def _show_guided_finding(self) -> None:
        if self._guided_index >= len(self._guided_findings):
            self._guided_active = False
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

    def on_key(self, event) -> None:
        """Handle key events during guided review."""
        if not self._guided_active:
            return

        if event.key == "space":
            # Pin current finding
            f = self._guided_findings[self._guided_index]
            f.pinned = True
            self._guided_index += 1
            self._show_guided_finding()
            event.prevent_default()
        elif event.key == "n":
            # Skip current finding
            self._guided_index += 1
            self._show_guided_finding()
            event.prevent_default()
        elif event.key == "escape":
            # Stop guided review
            self._guided_active = False
            pinned_count = sum(1 for f in self.findings if f.pinned)
            self.notify(f"Guided review stopped. {pinned_count} rules pinned.")
            event.prevent_default()

    def action_show_help(self) -> None:
        self.notify("1-4: Switch tabs | Space: Pin rule | F2: Save | g: Guided | e: View rows | q: Quit")
