"""GoldenMatch Interactive TUI -- main Textual application."""

from __future__ import annotations

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, TabbedContent, TabPane

from goldenmatch.tui.screens.autoconfig_screen import AutoConfigScreen
from goldenmatch.tui.sidebar import Sidebar
from goldenmatch.tui.tabs.boost_tab import BoostTab
from goldenmatch.tui.tabs.config_tab import ConfigTab
from goldenmatch.tui.tabs.controller_tab import ControllerTab
from goldenmatch.tui.tabs.corrections_tab import CorrectionsTab
from goldenmatch.tui.tabs.data_tab import DataTab
from goldenmatch.tui.tabs.export_tab import ExportTab
from goldenmatch.tui.tabs.golden_tab import GoldenTab
from goldenmatch.tui.tabs.matches_tab import MatchesTab
from goldenmatch.tui.widgets.progress_overlay import ProgressOverlay
from goldenmatch.tui.widgets.threshold_slider import ThresholdSlider


class GoldenMatchApp(App):
    """Interactive TUI for building GoldenMatch configs with live feedback."""

    TITLE = "⚡ GoldenMatch"

    CSS = """
    /* ── Gold/Amber Theme ─────────────────────────────────────────── */
    Screen {
        background: #1a1a2e;
    }
    Header {
        background: #d4a017;
        color: #1a1a2e;
        text-style: bold;
    }
    Footer {
        background: #16213e;
    }
    Footer > .footer--key {
        background: #d4a017 30%;
        color: #f0f0f0;
    }
    Footer > .footer--description {
        color: #8892a0;
    }
    TabbedContent > ContentSwitcher {
        background: #1a1a2e;
    }
    Tab {
        color: #8892a0;
    }
    Tab.-active {
        color: #d4a017;
        text-style: bold;
    }
    Tab:hover {
        color: #f0f0f0;
    }
    Underline > .underline--bar {
        color: #d4a017 40%;
    }
    DataTable {
        background: #16213e;
    }
    DataTable > .datatable--header {
        background: #d4a017 20%;
        color: #d4a017;
        text-style: bold;
    }
    DataTable > .datatable--cursor {
        background: #d4a017 40%;
        color: #f0f0f0;
    }
    Input {
        background: #16213e;
        border: solid #d4a017 40%;
        color: #f0f0f0;
    }
    Input:focus {
        border: solid #d4a017;
    }
    Select {
        background: #16213e;
        border: solid #d4a017 40%;
    }
    Button {
        background: #d4a017 20%;
        color: #f0f0f0;
        border: solid #d4a017 40%;
    }
    Button:hover {
        background: #d4a017 40%;
    }
    Button.-primary {
        background: #d4a017;
        color: #1a1a2e;
        text-style: bold;
    }
    Switch {
        background: #16213e;
    }
    Switch.-on > .switch--slider {
        color: #d4a017;
    }

    /* ── Layout ───────────────────────────────────────────────────── */
    #sidebar {
        width: 30;
        background: #16213e;
        border-right: solid #d4a017 40%;
        padding: 1;
    }
    #main {
        width: 1fr;
    }
    .sidebar-section {
        margin-bottom: 1;
    }
    .sidebar-label {
        color: #d4a017;
        text-style: bold;
    }
    .stat-value {
        color: #2ecc71;
    }

    /* ── Progress Overlay ─────────────────────────────────────────── */
    #progress-overlay {
        align: center middle;
        background: #1a1a2e 95%;
        width: 100%;
        height: 100%;
    }
    #progress-box {
        width: 60;
        height: auto;
        max-height: 30;
        background: #16213e;
        border: solid #d4a017;
        padding: 2 4;
    }
    .progress-title {
        color: #d4a017;
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
    }
    .progress-stage {
        color: #8892a0;
    }
    .progress-done {
        color: #2ecc71;
    }
    .progress-active {
        color: #d4a017;
        text-style: bold;
    }
    .progress-pending {
        color: #8892a0 50%;
    }

    /* ── Re-run Footer Bar ────────────────────────────────────────── */
    #rerun-bar {
        dock: bottom;
        height: 1;
        background: #d4a017 20%;
        color: #d4a017;
        padding: 0 2;
    }

    /* ── Auto-Config Screen ───────────────────────────────────────── */
    #autoconfig-screen {
        align: center middle;
        background: #1a1a2e;
    }
    #autoconfig-card {
        width: 70;
        height: auto;
        max-height: 40;
        background: #16213e;
        border: solid #d4a017;
        padding: 2 4;
    }
    .autoconfig-title {
        color: #d4a017;
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
    }
    .autoconfig-subtitle {
        color: #8892a0;
        text-align: center;
        margin-bottom: 1;
    }
    .autoconfig-info {
        color: #f0f0f0;
        margin-bottom: 1;
    }
    .autoconfig-buttons {
        align: center middle;
        height: 3;
        margin-top: 1;
    }

    /* ── Shortcut Overlay ─────────────────────────────────────────── */
    #shortcut-overlay {
        align: center middle;
        background: #1a1a2e 90%;
    }
    #shortcut-box {
        width: 50;
        height: auto;
        max-height: 30;
        background: #16213e;
        border: solid #d4a017;
        padding: 2 3;
    }
    .shortcut-title {
        color: #d4a017;
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
    }
    .shortcut-row {
        color: #f0f0f0;
    }
    .shortcut-key {
        color: #d4a017;
        text-style: bold;
    }
    """

    BINDINGS = [
        Binding("f1", "help", "Help"),
        Binding("f2", "save_config", "Save"),
        Binding("f5", "run_sample", "Run"),
        Binding("ctrl+a", "auto_configure", "Auto"),
        Binding("ctrl+r", "rerun", "Re-run"),
        Binding("ctrl+s", "save_preset", "Preset"),
        Binding("ctrl+e", "quick_export", "Export"),
        Binding("ctrl+t", "triage", "Triage"),
        Binding("question_mark", "show_shortcuts", "Shortcuts"),
        Binding("1", "goto_tab_1", "Data", show=False),
        Binding("2", "goto_tab_2", "Config", show=False),
        Binding("3", "goto_tab_3", "Matches", show=False),
        Binding("4", "goto_tab_4", "Golden", show=False),
        Binding("5", "goto_tab_5", "Boost", show=False),
        Binding("6", "goto_tab_6", "Export", show=False),
        Binding("7", "goto_tab_7", "Controller", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, files: list[str] | None = None, **kwargs):
        super().__init__(**kwargs)
        self.file_paths = files or []
        self.engine = None
        self.current_config = None
        self.last_result = None
        # Phase 4 (#437 surface sync): MemoryStore path consumed by the
        # Corrections tab + Golden / Matches modals. Set by callers
        # (e.g. CLI launcher) when memory is enabled in config.
        self.memory_db_path: str | None = None
        # Progress-overlay state (mounted while a sample/full run is in flight).
        self._progress_timer = None
        self._progress_elapsed = 0.0

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield Sidebar(id="sidebar")
            with Vertical(id="main"):
                with TabbedContent():
                    with TabPane("Data", id="tab-data"):
                        yield DataTab()
                    with TabPane("Config", id="tab-config"):
                        yield ConfigTab()
                    with TabPane("Matches", id="tab-matches"):
                        yield MatchesTab()
                    with TabPane("Golden", id="tab-golden"):
                        yield GoldenTab()
                    with TabPane("Boost", id="tab-boost"):
                        yield BoostTab()
                    with TabPane("Export", id="tab-export"):
                        yield ExportTab()
                    with TabPane("Controller", id="tab-controller"):
                        yield ControllerTab()
                    # Phase 4 of v1.18 surface-sync roadmap (#437):
                    # inspect Learning Memory corrections + delete /
                    # filter. Writes happen via Golden + Matches modals.
                    with TabPane("Corrections", id="tab-corrections"):
                        yield CorrectionsTab()
        yield Footer()

    def on_mount(self) -> None:
        """Load files on app startup if paths were provided."""
        if self.file_paths:
            self.load_files(self.file_paths)

    def load_files(self, paths: list[str]) -> None:
        """Load data files via the MatchEngine and update the UI."""
        from goldenmatch.tui.engine import MatchEngine

        try:
            self.engine = MatchEngine(paths)
            sidebar = self.query_one(Sidebar)
            sidebar.update_file_info(self.engine)
            data_tab = self.query_one(DataTab)
            data_tab.show_profile(self.engine)
            # Update config tab with available columns
            config_tab = self.query_one(ConfigTab)
            config_tab.set_columns(self.engine.columns)
        except Exception as e:
            self.notify(f"Error loading files: {e}", severity="error")

    def on_config_tab_config_changed(self, event: ConfigTab.ConfigChanged) -> None:
        """Handle config changes from the Config tab."""
        self.current_config = event.config
        # Update sidebar
        sidebar = self.query_one(Sidebar)
        sidebar.update_config(event.config)
        # Update export tab with current config
        export_tab = self.query_one(ExportTab)
        export_tab.set_config(event.config)
        # Run sample if engine is loaded
        if self.engine is not None:
            self._start_progress()
            self.run_matching(event.config)

    # ── Progress overlay ──────────────────────────────────────────

    def _start_progress(self) -> None:
        """Mount the full-screen progress overlay and tick elapsed time.

        The pipeline runs in a single worker call with no per-stage callback,
        so the overlay is a time-driven busy indicator (elapsed seconds + a
        bar that creeps toward 95% until completion), not a claim of real
        per-stage completion. It replaces the prior bare toast.
        """
        if self.query("#progress-overlay"):
            return
        self._progress_elapsed = 0.0
        self.mount(ProgressOverlay(id="progress-overlay"))
        self._progress_timer = self.set_interval(0.1, self._tick_progress)

    def _tick_progress(self) -> None:
        self._progress_elapsed += 0.1
        overlay = self.query("#progress-overlay")
        if not overlay:
            return
        # Creep toward 95% asymptotically; set_complete jumps to 100.
        pct = min(95.0, self._progress_elapsed * 12.0)
        overlay.first().update_progress(
            stage=0, percent=pct, elapsed=self._progress_elapsed
        )

    def _stop_progress(self) -> None:
        if self._progress_timer is not None:
            self._progress_timer.stop()
            self._progress_timer = None
        overlay = self.query("#progress-overlay")
        if overlay:
            node = overlay.first()
            node.set_complete()
            node.remove()

    @work(thread=True)
    def run_matching(self, config) -> None:
        """Run matching in a background thread."""
        if self.engine is None:
            return
        try:
            result = self.engine.run_sample(config, sample_size=1000)
            self.call_from_thread(self._on_matching_complete, result)
        except Exception as e:
            self.call_from_thread(self._stop_progress)
            self.call_from_thread(
                self.notify, f"Matching error: {e}", severity="error"
            )

    def _on_matching_complete(self, result) -> None:
        """Called on the main thread when matching finishes."""
        self._stop_progress()
        self.last_result = result
        # Update sidebar stats
        sidebar = self.query_one(Sidebar)
        sidebar.update_stats(result.stats)
        # Update matches tab
        matches_tab = self.query_one(MatchesTab)
        matches_tab.update_results(result, self.engine.data)
        # Update golden tab
        golden_tab = self.query_one(GoldenTab)
        golden_tab.update_results(result)
        # Update boost tab
        boost_tab = self.query_one(BoostTab)
        boost_tab.update_results(result, self.engine.data)
        self.notify("Sample matching complete.", severity="information")

    def on_boost_tab_boost_complete(self, event: BoostTab.BoostComplete) -> None:
        """Handle boost re-scoring: re-cluster and refresh all tabs."""
        if self.last_result is None or self.engine is None:
            return

        from goldenmatch.core.cluster import build_clusters
        from goldenmatch.tui.engine import EngineResult

        # Re-cluster with boosted scores using current threshold
        threshold = 0.5  # classifier probabilities: >0.5 = match
        filtered = [(a, b, s) for a, b, s in event.scored_pairs if s >= threshold]
        all_ids = sorted(set(
            mid for cinfo in self.last_result.clusters.values() for mid in cinfo["members"]
        ))
        clusters = build_clusters(filtered, all_ids)

        # Build updated result
        stats = self.engine._compute_stats(clusters, len(all_ids))
        updated = EngineResult(
            clusters=clusters,
            golden=self.last_result.golden,
            unique=self.last_result.unique,
            dupes=self.last_result.dupes,
            quarantine=self.last_result.quarantine,
            matched=self.last_result.matched,
            unmatched=self.last_result.unmatched,
            scored_pairs=event.scored_pairs,
            stats=stats,
        )
        self.last_result = updated
        self.engine._last_result = updated

        # Refresh tabs
        sidebar = self.query_one(Sidebar)
        sidebar.update_stats(stats)
        matches_tab = self.query_one(MatchesTab)
        matches_tab.update_results(updated, self.engine.data)
        golden_tab = self.query_one(GoldenTab)
        golden_tab.update_results(updated)

        self.notify(
            f"Boost applied! {len(filtered)} pairs above threshold, "
            f"{stats.total_clusters} clusters.",
            severity="information",
        )

    def action_help(self) -> None:
        """Show help information."""
        self.notify(
            "F1:Help  F2:Save Config  F5:Run Sample  "
            "Ctrl+R:Re-run  Ctrl+S:Save Preset  Q:Quit",
            title="Key Bindings",
        )

    def action_save_config(self) -> None:
        """Save current config to YAML via the export tab."""
        if self.current_config is None:
            self.notify("No config to save.", severity="warning")
            return
        export_tab = self.query_one(ExportTab)
        export_tab.set_config(self.current_config)
        export_tab._handle_save_config()

    def action_run_sample(self) -> None:
        """Run matching on a sample of the data."""
        if self.engine is None:
            self.notify("No files loaded.", severity="warning")
            return
        if self.current_config is None:
            self.notify("No config set. Build a config in the Config tab first.", severity="warning")
            return
        self.notify("Running sample match...", severity="information")
        self._start_progress()
        self.run_matching(self.current_config)

    def action_rerun(self) -> None:
        """Re-run with current config."""
        self.action_run_sample()

    def action_save_preset(self) -> None:
        """Save config as a named preset."""
        if self.current_config is None:
            self.notify("No config to save.", severity="warning")
            return
        export_tab = self.query_one(ExportTab)
        export_tab.set_config(self.current_config)
        export_tab._handle_save_preset()

    @work(thread=True)
    def run_full_job(self, config, output_options: dict) -> None:
        """Run full matching pipeline in a background thread."""
        if self.engine is None:
            return
        try:
            result = self.engine.run_full(config)
            self.call_from_thread(self._on_full_job_complete, result, output_options)
        except Exception as e:
            self.call_from_thread(self._on_full_job_error, str(e))

    def on_threshold_slider_threshold_changed(
        self, event: ThresholdSlider.ThresholdChanged
    ) -> None:
        """Live re-cluster preview: recompute the cluster count at the new
        threshold off cached scored pairs (no re-scoring) and update the slider."""
        if self.engine is None or self.last_result is None:
            return
        try:
            stats = self.engine.recluster_at_threshold(event.value)
        except RuntimeError:
            return
        try:
            slider = self.query_one("#threshold-slider", ThresholdSlider)
            slider.set_preview(stats.total_clusters)
        except Exception:
            pass

    def _on_full_job_complete(self, result, output_options: dict) -> None:
        """Handle completion of a full job run."""
        self.last_result = result
        # Update sidebar stats
        sidebar = self.query_one(Sidebar)
        sidebar.update_stats(result.stats)
        # Update matches and golden tabs
        matches_tab = self.query_one(MatchesTab)
        matches_tab.update_results(result, self.engine.data)
        golden_tab = self.query_one(GoldenTab)
        golden_tab.update_results(result)
        # Update boost tab
        boost_tab = self.query_one(BoostTab)
        boost_tab.update_results(result, self.engine.data)
        # Update export status
        export_tab = self.query_one(ExportTab)
        run_status = export_tab.query_one("#run-status")
        run_status.update("[green]Full job complete![/green]")
        self.notify("Full job complete.", severity="information")

    def _on_full_job_error(self, error_msg: str) -> None:
        """Handle error from a full job run."""
        export_tab = self.query_one(ExportTab)
        run_status = export_tab.query_one("#run-status")
        run_status.update(f"[red]Error: {error_msg}[/red]")
        self.notify(f"Full job error: {error_msg}", severity="error")

    # ── Tab navigation ────────────────────────────────────────────

    def _goto_tab(self, tab_id: str) -> None:
        """Switch to a specific tab."""
        try:
            tabbed = self.query_one(TabbedContent)
            tabbed.active = tab_id
        except Exception:
            pass

    def action_goto_tab_1(self) -> None:
        self._goto_tab("tab-data")

    def action_goto_tab_2(self) -> None:
        self._goto_tab("tab-config")

    def action_goto_tab_3(self) -> None:
        self._goto_tab("tab-matches")

    def action_goto_tab_4(self) -> None:
        self._goto_tab("tab-golden")

    def action_goto_tab_5(self) -> None:
        self._goto_tab("tab-boost")

    def action_goto_tab_6(self) -> None:
        self._goto_tab("tab-export")

    def action_goto_tab_7(self) -> None:
        self._goto_tab("tab-controller")

    # ── Auto-configure ────────────────────────────────────────────

    def action_auto_configure(self) -> None:
        """Run AutoConfigController on the loaded data and adopt the result.

        Mirrors the web workbench's "Auto-configure" button: profiles data,
        picks a config, captures stop_reason / decisions / indicators / NE,
        renders them in the Controller tab. Switches focus to the Controller
        tab so the user sees the telemetry immediately.
        """
        if self.engine is None:
            self.notify("No files loaded.", severity="warning")
            return
        self.notify("Running auto-configure…", severity="information")
        self._run_auto_configure_job()

    @work(thread=True)
    def _run_auto_configure_job(self) -> None:
        if self.engine is None:
            return
        try:
            config, telemetry = self.engine.auto_configure()
            self.call_from_thread(self._on_auto_configure_complete, config, telemetry)
        except Exception as e:
            self.call_from_thread(
                self.notify, f"Auto-configure error: {e}", severity="error"
            )

    def _on_auto_configure_complete(self, config, telemetry) -> None:
        """Adopt the controller's committed config + render telemetry."""
        self.current_config = config
        # Refresh sidebar config summary.
        sidebar = self.query_one(Sidebar)
        sidebar.update_config(config)
        # Push to the Config tab so the user can tweak from the suggestion.
        try:
            config_tab = self.query_one(ConfigTab)
            if hasattr(config_tab, "set_config"):
                config_tab.set_config(config)
        except Exception:
            # ConfigTab may not have a set_config; non-fatal.
            pass
        # Refresh export tab so save-config uses the auto-configured shape.
        export_tab = self.query_one(ExportTab)
        export_tab.set_config(config)
        # Render telemetry and switch to the Controller tab.
        controller_tab = self.query_one(ControllerTab)
        controller_tab.update_telemetry(telemetry)
        self._goto_tab("tab-controller")
        # Build a one-line status surfacing what the user got.
        n_decisions = len(getattr(telemetry.history, "entries", []) or [])
        ne_count = sum(
            len(getattr(mk, "negative_evidence", None) or [])
            for mk in config.get_matchkeys()
        )
        suffix = f" · {ne_count} NE field{'s' if ne_count != 1 else ''}" if ne_count else ""
        self.notify(
            f"Auto-configured · {n_decisions} controller iteration"
            f"{'s' if n_decisions != 1 else ''}{suffix}.",
            severity="information",
        )
        # Surface the detected config in a review screen (Run / Edit / Save).
        self._show_autoconfig_screen(config)

    def _autoconfig_column_profiles(self, config) -> list[dict]:
        """Flatten the committed config's matchkey fields into the row shape
        AutoConfigScreen renders (name / type / scorer / weight)."""
        rows: list[dict] = []
        for mk in config.get_matchkeys():
            for f in getattr(mk, "fields", None) or []:
                name = getattr(f, "field", None) or getattr(f, "resolved_field", None)
                if not name:
                    continue
                rows.append({
                    "name": name,
                    "type": getattr(mk, "type", "string"),
                    "scorer": getattr(f, "scorer", None) or "—",
                    "weight": getattr(f, "weight", None) or "—",
                })
        return rows

    def _show_autoconfig_screen(self, config) -> None:
        """Push the auto-config review screen; route its Run/Edit/Save result."""
        if self.engine is None:
            return
        mks = config.get_matchkeys()
        threshold = next(
            (mk.threshold for mk in mks if getattr(mk, "threshold", None) is not None),
            0.80,
        )
        blocking_info = "auto" if getattr(config, "blocking", None) else "none"
        screen = AutoConfigScreen(
            file_name=", ".join(p for p in self.file_paths) or "loaded data",
            row_count=self.engine.row_count,
            col_count=len(self.engine.columns),
            column_profiles=self._autoconfig_column_profiles(config),
            blocking_info=blocking_info,
            threshold=threshold,
            model_info="auto",
        )

        def _on_dismiss(result: str | None) -> None:
            if result == "run":
                self.action_run_sample()
            elif result == "edit":
                self._goto_tab("tab-config")
            elif result == "save":
                self.action_save_config()

        self.push_screen(screen, _on_dismiss)

    # ── Quick export ──────────────────────────────────────────────

    def action_quick_export(self) -> None:
        """Quick export with default settings."""
        if self.last_result is None:
            self.notify("No results to export. Run matching first.", severity="warning")
            return
        self._goto_tab("tab-export")
        self.notify("Switched to Export tab. Configure and save.", severity="information")

    # ── Guided triage ─────────────────────────────────────────────

    def action_triage(self) -> None:
        """Walk the borderline (review-band) pairs one at a time, recording
        approve/reject corrections to Learning Memory (Ctrl+T)."""
        if self.last_result is None or self.engine is None:
            self.notify("No results to triage. Run matching first.", severity="warning")
            return
        from goldenmatch.core.review_queue import gate_pairs

        _, review, _ = gate_pairs(self.last_result.scored_pairs)
        if not review:
            self.notify("No borderline pairs to review.", severity="information")
            return
        if not self.memory_db_path:
            self.notify(
                "Memory store not configured -- decisions won't persist. "
                "Launch with memory enabled to record corrections.",
                severity="warning",
            )

        from goldenmatch.tui.screens.triage_screen import TriageScreen

        dataset = getattr(self, "memory_dataset", None) or "tui"

        def _on_done(summary: dict | None) -> None:
            if not summary:
                return
            self.notify(
                f"Triage done -- approved {summary['approved']}, "
                f"rejected {summary['rejected']}, skipped {summary['skipped']}.",
                severity="information",
            )

        self.push_screen(
            TriageScreen(pairs=review, df=self.engine.data, dataset=dataset),
            _on_done,
        )

    # ── Shortcut overlay ──────────────────────────────────────────

    def action_show_shortcuts(self) -> None:
        """Show keyboard shortcuts overlay."""
        shortcuts = (
            "[bold #d4a017]Keyboard Shortcuts[/]\n\n"
            "[bold #d4a017]1-6[/]        Jump to tab\n"
            "[bold #d4a017]F5[/]         Run / re-run\n"
            "[bold #d4a017]F2[/]         Save config\n"
            "[bold #d4a017]Ctrl+R[/]     Re-run matching\n"
            "[bold #d4a017]Ctrl+S[/]     Save preset\n"
            "[bold #d4a017]Ctrl+E[/]     Quick export\n"
            "[bold #d4a017]Ctrl+T[/]     Triage borderline pairs\n"
            "[bold #d4a017]?[/]          This help\n"
            "[bold #d4a017]Q[/]          Quit\n\n"
            "[bold #8892a0]Matches Tab:[/]\n"
            "[bold #d4a017]↑/↓[/]        Navigate clusters\n"
            "[bold #d4a017]Enter[/]       Select cluster\n\n"
            "[bold #8892a0]Boost Tab:[/]\n"
            "[bold #d4a017]y[/]           Label as match\n"
            "[bold #d4a017]n[/]           Label as non-match\n"
            "[bold #d4a017]s[/]           Skip pair\n"
        )
        self.notify(shortcuts, title="⚡ GoldenMatch", timeout=10)
