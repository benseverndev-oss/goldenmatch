"""Golden tab inline-edit modal (#437 surface sync, Phase 4).

Spec: docs/superpowers/specs/2026-05-22-phase-4-tui-corrections-tab-design.md

Opens when the operator hits ``e`` on a Golden tab cell. Captures a
field-level Correction (``decision="field_correct"``) and writes it
to MemoryStore. Returns the Correction on save, None on cancel.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static


class GoldenEditModal(ModalScreen):
    """Inline-edit modal that captures a field-level correction.

    Use via:
        result = await self.app.push_screen_wait(
            GoldenEditModal(
                cluster_id=42, field_name="address1",
                original_value="1 Elm St", dataset="customers",
            )
        )

    Returns:
        The created Correction on save, None on cancel.
    """

    DEFAULT_CSS = """
    GoldenEditModal {
        align: center middle;
    }
    #modal-body {
        background: $panel;
        border: thick $primary;
        padding: 1 2;
        width: 70;
        height: auto;
    }
    #modal-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    .modal-row {
        height: auto;
        margin-bottom: 1;
    }
    .field-label {
        color: $text-muted;
        width: 16;
    }
    #modal-original {
        color: $text-muted;
    }
    Input {
        width: 1fr;
    }
    #modal-actions {
        height: auto;
        margin-top: 1;
    }
    #modal-actions Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        ("ctrl+s", "save", "Save"),
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        *,
        cluster_id: int,
        field_name: str,
        original_value: str,
        dataset: str,
        source: str = "steward",
    ) -> None:
        super().__init__()
        self.cluster_id = int(cluster_id)
        self.field_name = field_name
        self.original_value = original_value
        self.dataset = dataset
        self.source = source

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-body"):
            yield Static(
                f"Edit golden field: {self.field_name} (cluster {self.cluster_id})",
                id="modal-title",
            )
            with Horizontal(classes="modal-row"):
                yield Label("Original:", classes="field-label")
                yield Static(self.original_value or "(empty)", id="modal-original")
            with Horizontal(classes="modal-row"):
                yield Label("Corrected:", classes="field-label")
                yield Input(
                    value=self.original_value or "",
                    id="modal-corrected-input",
                )
            with Horizontal(classes="modal-row"):
                yield Label("Reason:", classes="field-label")
                yield Input(placeholder="optional", id="modal-reason-input")
            with Horizontal(id="modal-actions"):
                yield Button("Save (Ctrl+S)", id="modal-save", variant="primary")
                yield Button("Cancel (Esc)", id="modal-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "modal-save":
            self.action_save()
        elif event.button.id == "modal-cancel":
            self.action_cancel()

    def action_save(self) -> None:
        corrected = self.query_one("#modal-corrected-input", Input).value
        reason_input = self.query_one("#modal-reason-input", Input).value
        correction = _build_correction(
            cluster_id=self.cluster_id,
            field_name=self.field_name,
            original_value=self.original_value,
            corrected_value=corrected,
            dataset=self.dataset,
            source=self.source,
            reason=reason_input or None,
        )
        store = _get_store(self.app)
        if store is not None:
            try:
                store.add_correction(correction)
            finally:
                try:
                    store.close()
                except Exception:
                    pass
        self.dismiss(correction)

    def action_cancel(self) -> None:
        self.dismiss(None)


def _build_correction(
    *,
    cluster_id: int,
    field_name: str,
    original_value: str,
    corrected_value: str,
    dataset: str,
    source: str,
    reason: str | None,
) -> Any:
    """Construct a field-level Correction. Lazy import to keep
    importing this module cheap when goldenmatch is not installed."""
    from goldenmatch.core.memory.store import (
        HIGH_TRUST_SOURCES,
        Correction,
    )

    trust = 1.0 if source in {s.value for s in HIGH_TRUST_SOURCES} else 0.5
    return Correction(
        id=str(uuid.uuid4()),
        id_a=cluster_id,
        id_b=0,
        decision="field_correct",
        source=source,
        trust=trust,
        field_hash="",
        record_hash="",
        original_score=0.0,
        matchkey_name=None,
        reason=reason,
        dataset=dataset,
        created_at=datetime.now(),
        field_name=field_name,
        original_value=original_value,
        corrected_value=corrected_value,
    )


def _get_store(app: Any) -> Any:
    path = getattr(app, "memory_db_path", None)
    if not path:
        return None
    try:
        from goldenmatch.core.memory.store import MemoryStore
        return MemoryStore(backend="sqlite", path=path)
    except Exception:
        return None
