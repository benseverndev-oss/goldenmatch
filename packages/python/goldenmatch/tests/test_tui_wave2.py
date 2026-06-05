"""Wave 2: the three orphaned TUI components are now wired in.

- ProgressOverlay mounts during a run and is removed on completion.
- ThresholdSlider lives in the Matches tab, reveals on results, and drives a
  live re-cluster preview.
- AutoConfigScreen is pushed after auto-configure with a config summary.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.tui.app import GoldenMatchApp
from goldenmatch.tui.engine import EngineResult, EngineStats
from goldenmatch.tui.tabs.matches_tab import MatchesTab
from goldenmatch.tui.widgets.threshold_slider import ThresholdSlider


def _stats(total_clusters: int = 1) -> EngineStats:
    return EngineStats(
        total_records=3,
        total_clusters=total_clusters,
        singleton_count=1,
        match_rate=0.66,
        cluster_sizes=[2],
        avg_cluster_size=2.0,
        max_cluster_size=2,
        oversized_count=0,
    )


def _result() -> EngineResult:
    return EngineResult(
        clusters={0: {"members": [0, 1], "size": 2, "oversized": False, "confidence": 0.9}},
        golden=None,
        unique=None,
        dupes=None,
        quarantine=None,
        matched=None,
        unmatched=None,
        scored_pairs=[(0, 1, 0.88)],
        stats=_stats(),
    )


class TestProgressOverlay:
    @pytest.mark.asyncio
    async def test_overlay_mounts_and_removes(self, sample_csv):
        app = GoldenMatchApp(files=[str(sample_csv)])
        async with app.run_test() as pilot:
            await pilot.pause()
            app._start_progress()
            await pilot.pause()
            assert app.query("#progress-overlay")
            app._stop_progress()
            await pilot.pause()
            assert not app.query("#progress-overlay")


class TestThresholdSlider:
    @pytest.mark.asyncio
    async def test_slider_hidden_until_results(self, sample_csv):
        app = GoldenMatchApp(files=[str(sample_csv)])
        async with app.run_test() as pilot:
            await pilot.pause()
            slider = app.query_one("#threshold-slider", ThresholdSlider)
            assert slider.display is False

    @pytest.mark.asyncio
    async def test_slider_reveals_and_previews_on_results(self, sample_csv):
        app = GoldenMatchApp(files=[str(sample_csv)])
        async with app.run_test() as pilot:
            await pilot.pause()
            matches = app.query_one(MatchesTab)
            matches.update_results(_result(), pl.DataFrame({"__row_id__": [0, 1], "name": ["a", "a"]}))
            await pilot.pause()
            slider = app.query_one("#threshold-slider", ThresholdSlider)
            assert slider.display is True
            assert slider._preview_clusters == 1

    @pytest.mark.asyncio
    async def test_threshold_change_updates_preview(self, sample_csv):
        class _StubEngine:
            row_count = 3
            columns = ["name"]

            def recluster_at_threshold(self, t):
                # Higher threshold -> fewer clusters.
                return _stats(total_clusters=0 if t > 0.9 else 2)

        app = GoldenMatchApp(files=[str(sample_csv)])
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one(MatchesTab).update_results(
                _result(), pl.DataFrame({"__row_id__": [0, 1], "name": ["a", "a"]})
            )
            app.engine = _StubEngine()
            app.last_result = _result()
            await pilot.pause()

            app.on_threshold_slider_threshold_changed(
                ThresholdSlider.ThresholdChanged(0.6)
            )
            await pilot.pause()
            slider = app.query_one("#threshold-slider", ThresholdSlider)
            assert slider._preview_clusters == 2


def _weighted_cfg():
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="fuzzy_name",
                type="weighted",
                threshold=0.85,
                fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
            )
        ],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["name"])]),
    )


class TestAutoConfigScreen:
    def test_column_profiles_from_config(self):
        app = GoldenMatchApp(files=[])
        rows = app._autoconfig_column_profiles(_weighted_cfg())
        assert rows == [
            {"name": "name", "type": "weighted", "scorer": "jaro_winkler", "weight": 1.0}
        ]

    @pytest.mark.asyncio
    async def test_screen_pushed_after_autoconfig(self, sample_csv):
        from goldenmatch.tui.screens.autoconfig_screen import AutoConfigScreen

        cfg = _weighted_cfg()
        app = GoldenMatchApp(files=[str(sample_csv)])
        async with app.run_test() as pilot:
            await pilot.pause()
            app._show_autoconfig_screen(cfg)
            await pilot.pause()
            assert isinstance(app.screen, AutoConfigScreen)
