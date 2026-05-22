"""Corrections tab -- inspect MemoryStore corrections (#437 surface sync, Phase 4).

Spec: docs/superpowers/specs/2026-05-22-phase-4-tui-corrections-tab-design.md

Displays stored Learning Memory corrections in a DataTable.
Operators can refresh from disk (`r`), delete a selected correction
(`d` + confirm), or filter by dataset. Read-only inspection surface;
writes happen via the Golden tab inline-edit modal + Matches tab
pair-correction modal.

The MemoryStore path is read from ``GoldenMatchApp.memory_db_path``
(set when ``config.memory.enabled`` is True). When memory is
disabled the tab shows an empty-state message.
"""
from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Static


class CorrectionsTab(Static):
    """DataTable of MemoryStore corrections + filter/refresh affordances."""

    DEFAULT_CSS = """
    CorrectionsTab {
        height: 1fr;
        padding: 1;
    }
    #corrections-controls {
        height: auto;
        margin-bottom: 1;
    }
    #corrections-controls Input {
        width: 30;
    }
    #corrections-controls Button {
        margin: 0 1;
    }
    #corrections-table {
        height: 1fr;
    }
    #corrections-empty {
        padding: 2;
        color: $text-muted;
    }
    """

    BINDINGS = [
        ("r", "refresh", "Refresh"),
        ("d", "delete_selected", "Delete"),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._last_filter: str = ""

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(id="corrections-controls"):
                yield Input(
                    placeholder="filter by dataset",
                    id="corrections-filter",
                )
                yield Button("Refresh (r)", id="corrections-refresh")
                yield Button("Delete (d)", id="corrections-delete")
            yield DataTable(id="corrections-table", zebra_stripes=True)
            yield Static("", id="corrections-status")

    def on_mount(self) -> None:
        table: DataTable = self.query_one("#corrections-table", DataTable)
        table.cursor_type = "row"
        table.add_columns(
            "id", "decision", "id_a", "id_b", "field",
            "corrected", "dataset", "source", "trust", "created_at",
        )
        self.refresh_rows()

    def action_refresh(self) -> None:
        """Reload corrections from MemoryStore."""
        self.refresh_rows()

    def action_delete_selected(self) -> None:
        """Delete the highlighted correction. No-op when none selected."""
        table: DataTable = self.query_one("#corrections-table", DataTable)
        if not table.row_count or table.cursor_row is None:
            return
        try:
            row = table.get_row_at(table.cursor_row)
        except Exception:
            return
        correction_id = str(row[0])
        store = self._get_store()
        if store is None:
            self._set_status("Memory disabled -- cannot delete")
            return
        try:
            # MemoryStore may not have a `delete_correction` method on
            # older shapes; degrade gracefully.
            if hasattr(store, "delete_correction"):
                store.delete_correction(correction_id)
                self._set_status(f"Deleted correction {correction_id}")
            else:
                self._set_status(
                    "delete_correction not supported by this MemoryStore",
                )
        finally:
            try:
                store.close()
            except Exception:
                pass
        self.refresh_rows()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "corrections-filter":
            self._last_filter = (event.value or "").strip()
            self.refresh_rows()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "corrections-refresh":
            self.refresh_rows()
        elif event.button.id == "corrections-delete":
            self.action_delete_selected()

    def refresh_rows(self) -> None:
        """Reload rows from MemoryStore + render."""
        table: DataTable = self.query_one("#corrections-table", DataTable)
        table.clear()
        store = self._get_store()
        if store is None:
            self._set_status(
                "Memory disabled. Enable memory in config to use this tab.",
            )
            return
        try:
            dataset = self._last_filter or None
            corrections = list(store.get_corrections(dataset=dataset))
        finally:
            try:
                store.close()
            except Exception:
                pass
        for c in corrections:
            table.add_row(
                str(c.id)[:8],
                str(c.decision),
                str(c.id_a),
                str(c.id_b),
                str(getattr(c, "field_name", "") or ""),
                str(getattr(c, "corrected_value", "") or ""),
                str(c.dataset or ""),
                str(c.source),
                f"{float(c.trust):.2f}",
                c.created_at.isoformat(timespec="seconds") if c.created_at else "",
            )
        self._set_status(f"{len(corrections)} correction(s)")

    def _set_status(self, text: str) -> None:
        try:
            status = self.query_one("#corrections-status", Static)
            status.update(text)
        except Exception:
            pass

    def _get_store(self) -> Any:
        """Open the MemoryStore configured on the app, or None."""
        app = self.app
        path = getattr(app, "memory_db_path", None)
        if not path:
            return None
        try:
            from goldenmatch.core.memory.store import MemoryStore
        except ImportError:
            return None
        try:
            return MemoryStore(backend="sqlite", path=path)
        except Exception:
            return None
