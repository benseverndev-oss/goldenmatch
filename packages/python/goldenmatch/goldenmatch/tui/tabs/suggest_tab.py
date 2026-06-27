"""Suggestions tab -- config-healer suggestions for the loaded dataset (Task 9).

Lists ranked, self-verified suggestions from
``goldenmatch.core.suggest.review_config`` in a DataTable. Operators can
refresh (`r`) to (re-)run the healer over the current data + config, and apply
(`a`) the highlighted suggestion -- applying mutates the in-memory config via
the canonical ``goldenmatch.core.suggest.apply_suggestion`` write path and
re-runs sample matching through the app's existing run path (no duplicate
apply/patch logic here).

Fail-safe: the native kernel that powers suggestions is optional. When it's
absent the tab shows a "native required" message instead of erroring. Read
suggestions via the shared ``serialize_suggestions`` wire shape so the columns
match every other surface (REST/web/MCP/A2A).
"""
from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Static


class SuggestTab(Static):
    """DataTable of config-healer suggestions + refresh/apply affordances."""

    DEFAULT_CSS = """
    SuggestTab {
        height: 1fr;
        padding: 1;
    }
    #suggest-controls {
        height: auto;
        margin-bottom: 1;
    }
    #suggest-controls Button {
        margin: 0 1;
    }
    #suggest-table {
        height: 1fr;
    }
    #suggest-status {
        padding: 1 0;
        color: $text-muted;
    }
    """

    BINDINGS = [
        ("r", "refresh", "Refresh"),
        ("a", "apply_selected", "Apply"),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # serialized suggestion dicts (shared wire shape), parallel to table rows
        self._suggestions: list[dict] = []

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(id="suggest-controls"):
                yield Button("Refresh (r)", id="suggest-refresh")
                yield Button("Apply (a)", id="suggest-apply")
            yield DataTable(id="suggest-table", zebra_stripes=True)
            yield Static(
                "Press r to run the config healer over the loaded data.",
                id="suggest-status",
            )

    def on_mount(self) -> None:
        table: DataTable = self.query_one("#suggest-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("id", "kind", "target", "rationale", "verified")

    def action_refresh(self) -> None:
        """(Re-)run the healer over the current data + config and list results."""
        self.refresh_rows()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "suggest-refresh":
            self.action_refresh()
        elif event.button.id == "suggest-apply":
            self.action_apply_selected()

    def refresh_rows(self) -> None:
        """Run review_config and render serialized suggestions."""
        table: DataTable = self.query_one("#suggest-table", DataTable)
        table.clear()
        self._suggestions = []

        app = self.app
        engine = getattr(app, "engine", None)
        config = getattr(app, "current_config", None)
        df = getattr(engine, "data", None) if engine is not None else None
        if df is None or config is None:
            self._set_status("No data + config loaded. Run matching first.")
            return

        from goldenmatch.core.suggest import (
            SuggestionsNativeRequired,
            review_config,
        )
        from goldenmatch.core.suggest.surface import serialize_suggestions

        try:
            suggestions = review_config(df, config)
        except SuggestionsNativeRequired:
            self._set_status(
                "Suggestions need the native kernel -- pip install goldenmatch[native].",
            )
            return
        except Exception as exc:  # noqa: BLE001 - fail-safe UI handler
            self._set_status(f"review_config failed: {exc}")
            return

        self._suggestions = serialize_suggestions(suggestions, verified=True)
        for s in self._suggestions:
            table.add_row(
                str(s["id"]),
                str(s["kind"]),
                str(s["target"]),
                str(s["rationale"]),
                "yes" if s["verified"] else "no",
            )
        if self._suggestions:
            self._set_status(
                f"{len(self._suggestions)} suggestion(s). "
                "Highlight one and press a to apply.",
            )
        else:
            self._set_status("No suggestions -- config looks healthy.")

    def action_apply_selected(self) -> None:
        """Apply the highlighted suggestion to the config and re-run.

        Reuses the canonical ``apply_suggestion`` write path + the app's
        existing re-run path; no patch logic duplicated here.
        """
        table: DataTable = self.query_one("#suggest-table", DataTable)
        if not self._suggestions or table.cursor_row is None:
            self._set_status("Nothing to apply -- refresh first.")
            return
        idx = table.cursor_row
        if idx < 0 or idx >= len(self._suggestions):
            return

        app = self.app
        config = getattr(app, "current_config", None)
        engine = getattr(app, "engine", None)
        if config is None or engine is None:
            self._set_status("No config loaded -- cannot apply.")
            return

        # Re-run the healer to get the live Suggestion dataclass for this id
        # (the table holds serialized dicts; apply_suggestion needs the object).
        from goldenmatch.core.suggest import review_config
        from goldenmatch.core.suggest.apply import apply_suggestion

        target_id = self._suggestions[idx]["id"]
        try:
            suggestions = review_config(engine.data, config)
        except Exception as exc:  # noqa: BLE001 - fail-safe UI handler
            self._set_status(f"apply failed (re-review): {exc}")
            return

        match = next((s for s in suggestions if s.id == target_id), None)
        if match is None:
            self._set_status(
                f"Suggestion {target_id} no longer applies -- refresh.",
            )
            return

        try:
            new_config = apply_suggestion(config, match)
        except Exception as exc:  # noqa: BLE001 - fail-safe UI handler
            self._set_status(f"apply_suggestion failed: {exc}")
            return

        # Adopt + re-run via the app's existing config-change path.
        app.current_config = new_config
        try:
            app.run_matching(new_config)
        except Exception:
            pass
        self._set_status(f"Applied {target_id}; re-running matching.")
        try:
            app.notify(f"Applied suggestion {target_id}.", severity="information")
        except Exception:
            pass

    def _set_status(self, text: str) -> None:
        try:
            status = self.query_one("#suggest-status", Static)
            status.update(text)
        except Exception:
            pass
