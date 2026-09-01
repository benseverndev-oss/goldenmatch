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

    # The mount/remove test above drives `update_progress` only via the 0.1s
    # interval timer `_start_progress` installs, so whether it runs at all
    # between start and stop is a scheduling race -- `pilot.pause()` yields, it
    # does not wait 0.1s. That made coverage of this module swing between ~46%
    # and ~83% run to run (it tripped the coverage baseline gate on a commit
    # that touched neither the widget nor the app). `update_progress` is the
    # bulk of the file, and nothing asserted what it RENDERS. These call it
    # directly.

    @pytest.mark.asyncio
    async def test_update_progress_renders_stage_pairs_and_elapsed(self, sample_csv):
        from goldenmatch.tui.widgets.progress_overlay import PIPELINE_STAGES

        app = GoldenMatchApp(files=[str(sample_csv)])
        async with app.run_test() as pilot:
            await pilot.pause()
            app._start_progress()
            await pilot.pause()
            # Stop the 0.1s ticker before driving the widget by hand: it calls
            # update_progress(stage=0, ...) and would race whatever we set.
            app._progress_timer.stop()
            overlay = app.query_one("#progress-overlay")

            overlay.update_progress(stage=2, percent=40.0, pairs=1234, elapsed=3.5)
            await pilot.pause()

            stats = str(app.query_one("#progress-stats").render())
            assert PIPELINE_STAGES[2] in stats
            assert "1,234" in stats          # thousands separator
            assert "3.5s" in stats
            app._stop_progress()

    @pytest.mark.asyncio
    async def test_pipeline_view_marks_done_current_and_pending(self, sample_csv):
        from goldenmatch.tui.widgets.progress_overlay import PIPELINE_STAGES

        app = GoldenMatchApp(files=[str(sample_csv)])
        async with app.run_test() as pilot:
            await pilot.pause()
            app._start_progress()
            await pilot.pause()
            # Stop the 0.1s ticker before driving the widget by hand: it calls
            # update_progress(stage=0, ...) and would race whatever we set.
            app._progress_timer.stop()
            overlay = app.query_one("#progress-overlay")

            overlay.update_progress(stage=2, elapsed=1.0, stage_times={0: 0.4})
            await pilot.pause()

            pipeline = str(app.query_one("#progress-pipeline").render())
            assert f"✓ {PIPELINE_STAGES[0]}" in pipeline   # done
            assert f"● {PIPELINE_STAGES[2]}" in pipeline   # current
            assert f"○ {PIPELINE_STAGES[-1]}" in pipeline  # pending
            assert "0.4s" in pipeline                            # stage_times merged
            app._stop_progress()

    @pytest.mark.asyncio
    async def test_a_stage_past_the_end_falls_back_to_processing(self, sample_csv):
        app = GoldenMatchApp(files=[str(sample_csv)])
        async with app.run_test() as pilot:
            await pilot.pause()
            app._start_progress()
            await pilot.pause()
            # Stop the 0.1s ticker before driving the widget by hand: it calls
            # update_progress(stage=0, ...) and would race whatever we set.
            app._progress_timer.stop()
            overlay = app.query_one("#progress-overlay")

            overlay.update_progress(stage=999, elapsed=0.1)
            await pilot.pause()

            assert "Processing" in str(app.query_one("#progress-stats").render())
            app._stop_progress()

    @pytest.mark.asyncio
    async def test_set_complete_announces_completion(self, sample_csv):
        app = GoldenMatchApp(files=[str(sample_csv)])
        async with app.run_test() as pilot:
            await pilot.pause()
            app._start_progress()
            await pilot.pause()
            # Stop the 0.1s ticker before driving the widget by hand: it calls
            # update_progress(stage=0, ...) and would race whatever we set.
            app._progress_timer.stop()
            overlay = app.query_one("#progress-overlay")

            overlay.set_complete()
            await pilot.pause()

            assert "complete" in str(app.query_one("#progress-title").render()).lower()
            app._stop_progress()


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
