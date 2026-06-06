"""Wave 2.4: in-TUI guided review-queue triage loop (Ctrl+T).

Gates the current run's borderline pairs and walks them one at a time,
recording approve/reject corrections to Learning Memory.
"""
from __future__ import annotations

import pytest
from goldenmatch.tui.app import GoldenMatchApp
from goldenmatch.tui.engine import EngineResult, EngineStats
from goldenmatch.tui.screens.triage_screen import TriageScreen


def _stats() -> EngineStats:
    return EngineStats(
        total_records=2,
        total_clusters=1,
        singleton_count=0,
        match_rate=1.0,
        cluster_sizes=[2],
        avg_cluster_size=2.0,
        max_cluster_size=2,
        oversized_count=0,
    )


def _result(scored_pairs) -> EngineResult:
    return EngineResult(
        clusters={0: {"members": [0, 1], "size": 2, "oversized": False, "confidence": 0.8}},
        golden=None,
        unique=None,
        dupes=None,
        quarantine=None,
        matched=None,
        unmatched=None,
        scored_pairs=scored_pairs,
        stats=_stats(),
    )


class TestTriage:
    @pytest.mark.asyncio
    async def test_no_results_warns_not_crashes(self, sample_csv):
        app = GoldenMatchApp(files=[str(sample_csv)])
        async with app.run_test() as pilot:
            await pilot.pause()
            app.last_result = None
            app.action_triage()  # no results -> notify, no screen pushed
            await pilot.pause()
            assert not isinstance(app.screen, TriageScreen)

    @pytest.mark.asyncio
    async def test_no_borderline_pairs_skips(self, sample_csv):
        app = GoldenMatchApp(files=[str(sample_csv)])
        async with app.run_test() as pilot:
            await pilot.pause()
            # all pairs auto-merge (>0.95) -> nothing in the review band
            app.last_result = _result([(0, 1, 0.99)])
            app.action_triage()
            await pilot.pause()
            assert not isinstance(app.screen, TriageScreen)

    @pytest.mark.asyncio
    async def test_triage_records_decision(self, sample_csv, tmp_path):
        app = GoldenMatchApp(files=[str(sample_csv)])
        app.memory_db_path = str(tmp_path / "mem.db")
        async with app.run_test() as pilot:
            await pilot.pause()
            # 0.82 lands in the review band (0.75-0.95)
            app.last_result = _result([(0, 1, 0.82)])
            app.action_triage()
            await pilot.pause()
            assert isinstance(app.screen, TriageScreen)

            # approve the only pair -> screen exhausts and dismisses
            app.screen.action_approve()
            await pilot.pause()
            assert not isinstance(app.screen, TriageScreen)

        from goldenmatch.core.memory.store import MemoryStore

        store = MemoryStore(backend="sqlite", path=str(tmp_path / "mem.db"))
        assert store.count_corrections() == 1
