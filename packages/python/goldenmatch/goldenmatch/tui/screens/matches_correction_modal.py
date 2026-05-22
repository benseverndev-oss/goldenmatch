"""Matches tab pair-correction modal (#437 surface sync, Phase 4).

Spec: docs/superpowers/specs/2026-05-22-phase-4-tui-corrections-tab-design.md

Opens when the operator hits ``c`` on a Matches tab row. Captures a
pair-level Correction (``decision="approve"`` or ``"reject"``) and
writes it to MemoryStore. Returns the Correction on save, None on
cancel.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, Static


class MatchesCorrectionModal(ModalScreen):
    """Pair-correction modal: approve / reject + optional reason."""

    DEFAULT_CSS = """
    MatchesCorrectionModal {
        align: center middle;
    }
    #modal-body {
        background: $panel;
        border: thick $primary;
        padding: 1 2;
        width: 60;
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
        width: 12;
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
        id_a: int,
        id_b: int,
        score: float | None = None,
        dataset: str,
        matchkey_name: str | None = None,
        source: str = "steward",
    ) -> None:
        super().__init__()
        self.id_a = int(id_a)
        self.id_b = int(id_b)
        self.score = score
        self.dataset = dataset
        self.matchkey_name = matchkey_name
        self.source = source

    def compose(self) -> ComposeResult:
        score_text = (
            f"current score: {self.score:.2f}" if self.score is not None else ""
        )
        with Vertical(id="modal-body"):
            yield Static(
                f"Correct pair: row {self.id_a} <-> row {self.id_b}    {score_text}",
                id="modal-title",
            )
            with Horizontal(classes="modal-row"):
                yield Label("Decision:", classes="field-label")
                with RadioSet(id="modal-decision-set"):
                    yield RadioButton("Approve (these ARE the same)", value=True, id="modal-decision-approve")
                    yield RadioButton("Reject (these are NOT the same)", id="modal-decision-reject")
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
        approve_btn = self.query_one("#modal-decision-approve", RadioButton)
        decision = "approve" if approve_btn.value else "reject"
        reason = self.query_one("#modal-reason-input", Input).value
        correction = _build_correction(
            id_a=self.id_a,
            id_b=self.id_b,
            decision=decision,
            dataset=self.dataset,
            matchkey_name=self.matchkey_name,
            source=self.source,
            reason=reason or None,
            original_score=self.score or 0.0,
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
    id_a: int,
    id_b: int,
    decision: str,
    dataset: str,
    matchkey_name: str | None,
    source: str,
    reason: str | None,
    original_score: float,
) -> Any:
    from goldenmatch.core.memory.store import (
        HIGH_TRUST_SOURCES,
        Correction,
        _canon_pair,
    )

    ca, cb = _canon_pair(id_a, id_b)
    trust = 1.0 if source in {s.value for s in HIGH_TRUST_SOURCES} else 0.5
    return Correction(
        id=str(uuid.uuid4()),
        id_a=ca,
        id_b=cb,
        decision=decision,
        source=source,
        trust=trust,
        field_hash="",
        record_hash="",
        original_score=original_score,
        matchkey_name=matchkey_name,
        reason=reason,
        dataset=dataset,
        created_at=datetime.now(),
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
