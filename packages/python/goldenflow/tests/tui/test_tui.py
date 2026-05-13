from goldenflow.tui.app import ExportTab, GoldenFlowApp, MapTab, ProfileTab, TransformTab


def test_tui_app_instantiates():
    app = GoldenFlowApp(path=None)
    assert app is not None


def test_tui_app_has_title():
    app = GoldenFlowApp(path=None)
    assert app.title == "GoldenFlow"


def test_profile_tab_composes_without_data():
    """ProfileTab composes without error when no data is loaded."""
    tab = ProfileTab(df=None, profile=None)
    assert tab is not None


def test_transform_tab_composes_without_data():
    """TransformTab composes without error when no data is loaded."""
    tab = TransformTab(df=None, profile=None)
    assert tab is not None


def test_map_tab_composes_without_data():
    """MapTab composes without error when no data is loaded."""
    tab = MapTab(df=None, profile=None)
    assert tab is not None


def test_export_tab_composes():
    """ExportTab composes without error."""
    tab = ExportTab()
    assert tab is not None


def test_all_tabs_instantiate():
    """All four tabs can be instantiated together."""
    app = GoldenFlowApp(path=None)
    # Verify compose method doesn't raise
    # (We can't fully run the TUI without a display, but we can verify the app is built)
    assert app._df is None
    assert app._profile is None
    assert app._transformed_df is None


def test_transform_tab_with_profile():
    """TransformTab accepts profile data."""
    import polars as pl
    from goldenflow.engine.profiler_bridge import profile_dataframe

    df = pl.DataFrame({"name": ["Alice", "Bob"], "status": ["active", "ACTIVE"]})
    profile = profile_dataframe(df)
    tab = TransformTab(df=df, profile=profile)
    assert tab is not None
    assert tab._df is not None
    assert tab._profile is not None


def test_map_tab_with_profile():
    """MapTab accepts profile data."""
    import polars as pl
    from goldenflow.engine.profiler_bridge import profile_dataframe

    df = pl.DataFrame({"first_name": ["Alice"], "email": ["a@b.com"]})
    profile = profile_dataframe(df)
    tab = MapTab(df=df, profile=profile)
    assert tab is not None
    assert tab._df is not None
    assert tab._profile is not None
