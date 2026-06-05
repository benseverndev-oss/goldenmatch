"""Tests for Textual TUI."""
import pytest

try:
    from goldenpipe.tui.app import GoldenPipeApp
    from textual.app import App  # noqa: F401
    HAS_TEXTUAL = True
except ImportError:
    HAS_TEXTUAL = False

pytestmark = pytest.mark.skipif(not HAS_TEXTUAL, reason="textual not installed")


class TestGoldenPipeApp:
    async def test_app_launches(self):
        app = GoldenPipeApp()
        async with app.run_test(size=(120, 40)) as _pilot:
            assert app.title == "GoldenPipe"

    async def test_tabs_exist(self):
        app = GoldenPipeApp()
        async with app.run_test(size=(120, 40)) as _pilot:
            tabs = app.query("Tab")
            assert len(tabs) == 4

    async def test_tab_labels(self):
        app = GoldenPipeApp()
        async with app.run_test(size=(120, 40)) as _pilot:
            tab_labels = [t.label.plain for t in app.query("Tab")]
            assert "Pipeline" in tab_labels
            assert "Config" in tab_labels
            assert "Results" in tab_labels
            assert "Log" in tab_labels


class TestGoldenPipeAppWiring:
    """Wave 2.2: the four tabs are wired to a real PipeResult."""

    async def test_no_source_run_warns_not_crashes(self):
        app = GoldenPipeApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.action_run()  # no source -> notify only
            await pilot.pause()
            assert app.result is None

    async def test_render_result_populates_tabs(self, sample_csv):
        import goldenpipe
        from textual.widgets import DataTable, Static

        result = goldenpipe.run(str(sample_csv))
        app = GoldenPipeApp(source=str(sample_csv))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._render_result(result)
            await pilot.pause()
            # Pipeline tab has a row per stage.
            ptable = app.query_one("#pipeline-table", DataTable)
            assert ptable.row_count == len(result.stages)
            # Config tab shows the stage chain.
            assert "Stage chain" in app.query_one("#config-view", Static).render().plain
            assert app.result is result

    async def test_action_run_executes_pipeline(self, sample_csv):
        from textual.widgets import DataTable

        app = GoldenPipeApp(source=str(sample_csv))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.action_run()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.result is not None
            assert app.query_one("#pipeline-table", DataTable).row_count >= 1
