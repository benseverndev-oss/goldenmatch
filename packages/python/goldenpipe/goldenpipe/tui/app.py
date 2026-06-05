"""GoldenPipe TUI -- 4-tab pipeline interface (Pipeline / Config / Results / Log).

Wired to the real pipeline: press ``r`` to run the loaded source through
``goldenpipe.run`` and the tabs populate from the resulting ``PipeResult``
(stage statuses + timing, the stage chain, artifacts, and the reasoning log).
"""
from __future__ import annotations

try:
    from textual import work
    from textual.app import App, ComposeResult
    from textual.widgets import (
        DataTable,
        Footer,
        Header,
        Static,
        TabbedContent,
        TabPane,
    )
    HAS_TEXTUAL = True
except ImportError:
    HAS_TEXTUAL = False


if HAS_TEXTUAL:
    _STATUS_COLOR = {
        "success": "#2ecc71",
        "skipped": "#8892a0",
        "failed": "red",
        "partial": "#d4a017",
    }

    def _summarize(val) -> str:
        """One-line summary of an artifact value for the Results table."""
        try:
            height = getattr(val, "height", None)
            if height is not None:  # polars DataFrame
                width = getattr(val, "width", "?")
                return f"{height} rows x {width} cols"
            if isinstance(val, (list, tuple, set, dict)):
                return f"{len(val)} items"
            return str(val)[:60]
        except Exception:
            return type(val).__name__

    class GoldenPipeApp(App):
        """GoldenPipe interactive TUI."""

        TITLE = "GoldenPipe"

        BINDINGS = [
            ("q", "quit", "Quit"),
            ("r", "run", "Run"),
            ("1", "tab_pipeline", "Pipeline"),
            ("2", "tab_config", "Config"),
            ("3", "tab_results", "Results"),
            ("4", "tab_log", "Log"),
        ]

        def __init__(self, source: str | None = None, config_path: str | None = None, **kwargs):
            super().__init__(**kwargs)
            self.source = source
            self.config_path = config_path
            self.result = None

        def compose(self) -> ComposeResult:
            yield Header()
            with TabbedContent():
                with TabPane("Pipeline", id="tab-pipeline"):
                    yield Static("", id="pipeline-hint")
                    yield DataTable(id="pipeline-table")
                with TabPane("Config", id="tab-config"):
                    yield Static("", id="config-view")
                with TabPane("Results", id="tab-results"):
                    yield DataTable(id="results-table")
                with TabPane("Log", id="tab-log"):
                    yield Static("", id="log-view")
            yield Footer()

        def on_mount(self) -> None:
            self.query_one("#pipeline-table", DataTable).add_columns(
                "Stage", "Status", "Time (s)"
            )
            self.query_one("#results-table", DataTable).add_columns(
                "Artifact", "Type", "Summary"
            )
            hint = self.query_one("#pipeline-hint", Static)
            if self.source:
                hint.update(
                    f"Source: [bold]{self.source}[/]   ·   press [bold #d4a017]r[/] to run"
                )
                self.query_one("#config-view", Static).update(
                    f"Source: [bold]{self.source}[/]\n"
                    f"Config: [bold]{self.config_path or 'auto (check -> flow -> dedupe)'}[/]"
                )
            else:
                hint.update(
                    "[yellow]No source loaded.[/] "
                    "Launch with a file: goldenpipe interactive data.csv"
                )

        # ── Run ───────────────────────────────────────────────────────

        def action_run(self) -> None:
            if not self.source:
                self.notify("No source to run. Launch with a data file.", severity="warning")
                return
            self.notify("Running pipeline...", severity="information")
            self._run_pipeline()

        @work(thread=True)
        def _run_pipeline(self) -> None:
            try:
                from goldenpipe._api import run as run_pipeline

                result = run_pipeline(self.source, config=self.config_path)
                self.call_from_thread(self._render_result, result)
            except Exception as e:  # pragma: no cover - surfaced as a toast
                self.call_from_thread(
                    self.notify, f"Pipeline error: {e}", severity="error"
                )

        def _render_result(self, result) -> None:
            """Populate all four tabs from a PipeResult."""
            self.result = result

            # Pipeline tab: stage statuses + per-stage timing.
            ptable = self.query_one("#pipeline-table", DataTable)
            ptable.clear()
            for name, sr in result.stages.items():
                status = sr.status.value
                color = _STATUS_COLOR.get(status, "white")
                t = result.timing.get(name, 0.0)
                ptable.add_row(name, f"[{color}]{status}[/]", f"{t:.3f}")
            for name in result.skipped:
                if name not in result.stages:
                    ptable.add_row(name, "[#8892a0]skipped[/]", "-")
            self.query_one("#pipeline-hint", Static).update(
                f"Source: [bold]{result.source}[/]   ·   "
                f"rows: [bold #2ecc71]{result.input_rows:,}[/]   ·   "
                f"status: [{_STATUS_COLOR.get(result.status.value, 'white')}]{result.status.value}[/]"
            )

            # Config tab: the realized stage chain.
            chain = " -> ".join(result.stages.keys()) or "(none)"
            self.query_one("#config-view", Static).update(
                f"Source: [bold]{result.source}[/]\n"
                f"Config: [bold]{self.config_path or 'auto'}[/]\n\n"
                f"[bold #d4a017]Stage chain:[/] {chain}"
            )

            # Results tab: artifacts browser.
            rtable = self.query_one("#results-table", DataTable)
            rtable.clear()
            for key, val in result.artifacts.items():
                rtable.add_row(key, type(val).__name__, _summarize(val))

            # Log tab: reasoning per stage + errors + total time.
            lines = [f"[bold #d4a017]Total time:[/] {sum(result.timing.values()):.3f}s\n"]
            if result.errors:
                lines.append("[bold red]Errors:[/]")
                lines.extend(f"  {e}" for e in result.errors)
                lines.append("")
            lines.append("[bold #d4a017]Reasoning:[/]")
            if result.reasoning:
                lines.extend(f"  [bold]{name}[/]: {why}" for name, why in result.reasoning.items())
            else:
                lines.append("  [dim](no reasoning recorded)[/]")
            self.query_one("#log-view", Static).update("\n".join(lines))

            self.notify("Pipeline complete.", severity="information")

        # ── Tab navigation ────────────────────────────────────────────

        def action_tab_pipeline(self) -> None:
            self.query_one(TabbedContent).active = "tab-pipeline"

        def action_tab_config(self) -> None:
            self.query_one(TabbedContent).active = "tab-config"

        def action_tab_results(self) -> None:
            self.query_one(TabbedContent).active = "tab-results"

        def action_tab_log(self) -> None:
            self.query_one(TabbedContent).active = "tab-log"
