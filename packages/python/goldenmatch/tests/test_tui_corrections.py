"""Phase 4 of v1.18 surface-sync roadmap: TUI Corrections tab + modals.

Spec: docs/superpowers/specs/2026-05-22-phase-4-tui-corrections-tab-design.md

Covers:
- CorrectionsTab launches without crash + reads seeded MemoryStore
- GoldenEditModal writes a field-level Correction on save
- MatchesCorrectionModal writes a pair-level Correction on save

These tests use Textual's pilot harness. They intentionally do
NOT exercise the Golden / Matches tab key-binding wiring (that's a
follow-up; the modals are independently tested by mounting them
directly via `push_screen_wait`).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

import pytest
from goldenmatch.tui.app import GoldenMatchApp


def _seed_store(path: Path) -> None:
    """Seed a MemoryStore with one pair-level + one field-level correction."""
    from goldenmatch.core.memory.store import Correction, MemoryStore

    store = MemoryStore(backend="sqlite", path=str(path))
    store.add_correction(Correction(
        id=str(uuid.uuid4()), id_a=1, id_b=2,
        decision="approve", source="steward", trust=1.0,
        field_hash="", record_hash="", original_score=0.95,
        matchkey_name=None, reason=None, dataset="customers",
        created_at=datetime.now(),
    ))
    store.add_correction(Correction(
        id=str(uuid.uuid4()), id_a=42, id_b=0,
        decision="field_correct", source="steward", trust=1.0,
        field_hash="", record_hash="", original_score=0.0,
        matchkey_name=None, reason="USPS", dataset="customers",
        created_at=datetime.now(),
        field_name="address1",
        original_value="1 Elm St",
        corrected_value="1 Elm Street, Apt 4B",
    ))
    store.close()


@pytest.mark.asyncio
async def test_corrections_tab_renders_seeded_store(tmp_path, sample_csv):
    """8th tab loads + DataTable shows the 2 seeded corrections."""
    db_path = tmp_path / "memory.db"
    _seed_store(db_path)

    app = GoldenMatchApp(files=[str(sample_csv)])
    app.memory_db_path = str(db_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from goldenmatch.tui.tabs.corrections_tab import CorrectionsTab
        tab = app.query_one(CorrectionsTab)
        # Force the in-memory refresh AFTER memory_db_path is set on app.
        tab.refresh_rows()
        await pilot.pause()
        table = tab.query_one("#corrections-table")
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_corrections_tab_empty_state_when_memory_disabled(sample_csv):
    """No memory_db_path -> tab loads but shows empty-state status."""
    app = GoldenMatchApp(files=[str(sample_csv)])
    # memory_db_path intentionally unset (defaults to None)
    async with app.run_test() as pilot:
        await pilot.pause()
        from goldenmatch.tui.tabs.corrections_tab import CorrectionsTab
        tab = app.query_one(CorrectionsTab)
        table = tab.query_one("#corrections-table")
        assert table.row_count == 0


@pytest.mark.asyncio
async def test_golden_edit_modal_writes_field_correction(tmp_path, sample_csv):
    """GoldenEditModal Save -> MemoryStore has a field_correct row."""
    from goldenmatch.core.memory.store import MemoryStore
    from goldenmatch.tui.screens.golden_edit_modal import GoldenEditModal

    db_path = tmp_path / "memory.db"
    app = GoldenMatchApp(files=[str(sample_csv)])
    app.memory_db_path = str(db_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = GoldenEditModal(
            cluster_id=42,
            field_name="address1",
            original_value="1 Elm St",
            dataset="customers",
        )

        async def _push() -> None:
            result = await app.push_screen_wait(modal)
            app._test_modal_result = result  # type: ignore[attr-defined]

        task = pilot.app.run_worker(_push, exclusive=True)
        await pilot.pause()
        # The modal's Input pre-populates with original_value; just save.
        modal.action_save()
        await pilot.pause()
        task.cancel()
        # Verify MemoryStore got the field-level row.
        store = MemoryStore(backend="sqlite", path=str(db_path))
        rows = list(store.get_corrections(dataset="customers"))
        store.close()
        assert len(rows) == 1
        assert rows[0].decision == "field_correct"
        assert rows[0].field_name == "address1"


@pytest.mark.asyncio
async def test_matches_correction_modal_writes_pair_correction(tmp_path, sample_csv):
    """MatchesCorrectionModal Save -> MemoryStore has approve/reject row."""
    from goldenmatch.core.memory.store import MemoryStore
    from goldenmatch.tui.screens.matches_correction_modal import (
        MatchesCorrectionModal,
    )

    db_path = tmp_path / "memory.db"
    app = GoldenMatchApp(files=[str(sample_csv)])
    app.memory_db_path = str(db_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = MatchesCorrectionModal(
            id_a=5, id_b=10, score=0.82, dataset="customers",
        )

        async def _push() -> None:
            result = await app.push_screen_wait(modal)
            app._test_modal_result = result  # type: ignore[attr-defined]

        task = pilot.app.run_worker(_push, exclusive=True)
        await pilot.pause()
        modal.action_save()
        await pilot.pause()
        task.cancel()
        store = MemoryStore(backend="sqlite", path=str(db_path))
        rows = list(store.get_corrections(dataset="customers"))
        store.close()
        assert len(rows) == 1
        # Pair is canonicalized (min, max).
        assert rows[0].id_a == 5
        assert rows[0].id_b == 10
        assert rows[0].decision == "approve"  # default radio selection
