"""Guided review-queue triage screen.

Walks the current run's borderline (review-band) pairs one at a time and
records an approve/reject Correction per decision -- the in-TUI equivalent of
GoldenCheck's guided review and the `goldenmatch review` CLI loop. Reuses the
correction-writing path from the Matches correction modal.
"""
from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Center, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static


class TriageScreen(Screen):
    """Sequentially triage borderline pairs (y=approve, n=reject, s=skip)."""

    BINDINGS = [
        ("y", "approve", "Approve"),
        ("n", "reject", "Reject"),
        ("s", "skip", "Skip"),
        ("q", "quit_triage", "Quit"),
    ]

    def __init__(
        self,
        *,
        pairs: list[tuple[int, int, float]],
        df: Any,
        dataset: str = "tui",
        source: str = "steward",
    ) -> None:
        super().__init__()
        self._pairs = list(pairs)
        self._dataset = dataset
        self._source = source
        self._idx = 0
        self._approved = 0
        self._rejected = 0
        self._skipped = 0
        self._row_lookup: dict[int, dict] = {}
        self._display_cols: list[str] = []
        if df is not None:
            self._row_lookup = {r["__row_id__"]: r for r in df.to_dicts()}
            self._display_cols = [c for c in df.columns if not c.startswith("__")][:6]

    def compose(self) -> ComposeResult:
        yield Header()
        with Center():
            with Vertical(id="triage-card"):
                yield Static("", id="triage-progress", classes="autoconfig-title")
                yield DataTable(id="triage-table")
                yield Static(
                    "[#8892a0]y = approve   n = reject   s = skip   q = quit[/]",
                    id="triage-hint",
                )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#triage-table", DataTable)
        table.add_columns("Field", "Record A", "Record B")
        self._show_current()

    def _show_current(self) -> None:
        progress = self.query_one("#triage-progress", Static)
        table = self.query_one("#triage-table", DataTable)
        table.clear()
        if self._idx >= len(self._pairs):
            self._finish()
            return
        id_a, id_b, score = self._pairs[self._idx]
        progress.update(
            f"[bold #d4a017]Pair {self._idx + 1}/{len(self._pairs)}[/]  "
            f"rows {id_a} <-> {id_b}  ·  score {score:.3f}"
        )
        row_a = self._row_lookup.get(id_a, {})
        row_b = self._row_lookup.get(id_b, {})
        for col in self._display_cols:
            va = str(row_a.get(col, ""))[:50]
            vb = str(row_b.get(col, ""))[:50]
            table.add_row(col, va, vb)

    def _decide(self, decision: str) -> None:
        if self._idx >= len(self._pairs):
            return
        id_a, id_b, score = self._pairs[self._idx]
        from goldenmatch.tui.screens.matches_correction_modal import (
            _build_correction,
            _get_store,
        )

        correction = _build_correction(
            id_a=id_a,
            id_b=id_b,
            decision=decision,
            dataset=self._dataset,
            matchkey_name=None,
            source=self._source,
            reason=None,
            original_score=score,
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
        self._idx += 1
        self._show_current()

    def action_approve(self) -> None:
        self._approved += 1
        self._decide("approve")

    def action_reject(self) -> None:
        self._rejected += 1
        self._decide("reject")

    def action_skip(self) -> None:
        self._skipped += 1
        self._idx += 1
        self._show_current()

    def action_quit_triage(self) -> None:
        self._finish()

    def _finish(self) -> None:
        self.dismiss({
            "approved": self._approved,
            "rejected": self._rejected,
            "skipped": self._skipped,
        })
