from goldenflow.tui.app import GoldenFlowApp


def test_tui_app_instantiates():
    app = GoldenFlowApp(path=None)
    assert app is not None


def test_tui_app_has_title():
    app = GoldenFlowApp(path=None)
    assert app.title == "GoldenFlow"
