"""Tests for Textual TUI."""
import pytest

try:
    from textual.app import App  # noqa: F401
    from goldenpipe.tui.app import GoldenPipeApp
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
