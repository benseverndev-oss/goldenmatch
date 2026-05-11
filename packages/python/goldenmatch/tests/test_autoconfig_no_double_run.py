"""Task 5.2: Verify zero-config callers call auto_configure_df pre-pipeline.

After Task 5.2, dedupe_df / match_df zero-config paths must:
  1. Call auto_configure_df *before* passing to _run_*_pipeline.
  2. Pass auto_config=False (or omit) to the pipeline.

This eliminates the double-pipeline-run failure mode introduced by Task 5.1
(auto_configure_df now runs the controller's iteration loop, which itself
calls dedupe_df/match_df on samples).
"""
from unittest.mock import patch

import polars as pl


def _trivial_dedupe_pipeline_return(df: pl.DataFrame) -> dict:
    """Minimal valid return shape for _run_dedupe_pipeline."""
    return {
        "golden": None,
        "clusters": {},
        "dupes": None,
        "unique": df,
        "scored_pairs": [],
        "memory_stats": None,
        "postflight_report": None,
        "quarantine": None,
    }


def _trivial_match_pipeline_return(df: pl.DataFrame) -> dict:
    return {
        "matched": None,
        "unmatched": df,
        "report": None,
        "quarantine": None,
        "postflight_report": None,
        "memory_stats": None,
    }


def test_dedupe_df_zero_config_does_not_pass_auto_config_true():
    """After Task 5.2, the caller's invocation of run_dedupe_df must
    pass auto_config=False (or not set the kwarg). The pipeline never re-runs
    auto-config when the caller already provided a config."""
    import goldenmatch as gm

    df = pl.DataFrame({
        "name": ["alice", "alyce", "bob"] * 4,
        "city": ["nyc", "la", "sf"] * 4,
    })
    with patch("goldenmatch.core.pipeline.run_dedupe_df") as mock_pipeline:
        mock_pipeline.return_value = _trivial_dedupe_pipeline_return(df)
        gm.dedupe_df(df)
    # No invocation of run_dedupe_df should have auto_config=True
    for call in mock_pipeline.call_args_list:
        kwargs = call.kwargs
        # Either kwarg absent, or explicitly False
        assert kwargs.get("auto_config", False) is False, (
            f"caller still passes auto_config=True: {call}"
        )


def test_match_df_zero_config_does_not_pass_auto_config_true():
    import goldenmatch as gm

    target = pl.DataFrame({"id": ["1", "2"] * 5, "title": ["foo", "bar"] * 5})
    ref = pl.DataFrame({"id": ["10", "20"] * 5, "title": ["foo", "baz"] * 5})
    with patch("goldenmatch.core.pipeline.run_match_df") as mock_pipeline:
        mock_pipeline.return_value = _trivial_match_pipeline_return(target)
        gm.match_df(target, ref)
    for call in mock_pipeline.call_args_list:
        kwargs = call.kwargs
        assert kwargs.get("auto_config", False) is False, (
            f"caller still passes auto_config=True: {call}"
        )


def test_dedupe_df_explicit_config_unaffected():
    """When config=GoldenMatchConfig is provided explicitly, no auto-config is invoked."""
    import goldenmatch as gm
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    df = pl.DataFrame({"email": ["a@x", "a@x", "b@y"]})
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="exact",
            fields=[MatchkeyField(field="email", transforms=["lowercase"])],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["email"], transforms=["lowercase"])],
            max_block_size=5000, skip_oversized=False,
        ),
    )
    with patch("goldenmatch.core.autoconfig.auto_configure_df") as mock_auto:
        _result = gm.dedupe_df(df, config=cfg)
    mock_auto.assert_not_called()


def test_dedupe_df_zero_config_invokes_auto_configure_df_once():
    """Zero-config caller invokes auto_configure_df exactly once before pipeline."""
    import goldenmatch as gm
    from goldenmatch.config.schemas import GoldenMatchConfig

    df = pl.DataFrame({"name": ["a", "b", "c"] * 4, "city": ["x", "y", "z"] * 4})
    with patch("goldenmatch._api.auto_configure_df") as mock_auto:
        mock_auto.return_value = GoldenMatchConfig(matchkeys=[])
        with patch("goldenmatch.core.pipeline.run_dedupe_df") as mock_pipeline:
            mock_pipeline.return_value = _trivial_dedupe_pipeline_return(df)
            gm.dedupe_df(df)
    # auto_configure_df called once with the dataframe
    mock_auto.assert_called_once()


def test_match_df_zero_config_invokes_auto_configure_df_with_reference():
    import goldenmatch as gm
    from goldenmatch.config.schemas import GoldenMatchConfig

    target = pl.DataFrame({"id": ["1"] * 6, "title": ["foo"] * 6})
    ref = pl.DataFrame({"id": ["10"] * 6, "title": ["bar"] * 6})
    with patch("goldenmatch._api.auto_configure_df") as mock_auto:
        mock_auto.return_value = GoldenMatchConfig(matchkeys=[])
        with patch("goldenmatch.core.pipeline.run_match_df") as mock_pipeline:
            mock_pipeline.return_value = _trivial_match_pipeline_return(target)
            gm.match_df(target, ref)
    mock_auto.assert_called_once()
    # The mock should have been called with reference= kwarg
    call = mock_auto.call_args_list[0]
    # Either positional or keyword
    if "reference" in call.kwargs:
        assert call.kwargs["reference"] is not None


# ============================================================
# Fix 1 — PostflightReport wired with controller_profile/history
# ============================================================

def test_dedupe_df_zero_config_postflight_has_controller_fields():
    """Zero-config dedupe_df should populate controller_profile and
    controller_history on the returned postflight_report."""
    import goldenmatch as gm
    from goldenmatch.config.schemas import GoldenMatchConfig
    from goldenmatch.core.autoconfig_history import RunHistory
    from goldenmatch.core.complexity_profile import ComplexityProfile

    df = pl.DataFrame({
        "name": ["alice", "alyce", "bob", "bobby"] * 4,
        "city": ["nyc", "la", "sf", "nyc"] * 4,
    })
    with patch("goldenmatch._api.auto_configure_df") as mock_auto:
        mock_auto.return_value = GoldenMatchConfig(matchkeys=[])
        # Seed the ContextVar with a fake (profile, history) so the _api wiring
        # can pick it up without running the real controller.
        from goldenmatch.core.autoconfig import _LAST_CONTROLLER_RUN
        fake_profile = ComplexityProfile()
        fake_history = RunHistory()
        _LAST_CONTROLLER_RUN.set((fake_profile, fake_history))

        with patch("goldenmatch.core.pipeline.run_dedupe_df") as mock_pipeline:
            mock_pipeline.return_value = _trivial_dedupe_pipeline_return(df)
            result = gm.dedupe_df(df)

    pf = result.postflight_report
    assert pf is not None, "postflight_report should not be None in zero-config path"
    assert pf.controller_profile is fake_profile, (
        "controller_profile should be the profile from _LAST_CONTROLLER_RUN"
    )
    assert pf.controller_history is fake_history, (
        "controller_history should be the history from _LAST_CONTROLLER_RUN"
    )


# ============================================================
# Fix 4 — zero-config path passes _skip_finalize=True
# ============================================================

def test_dedupe_df_zero_config_passes_skip_finalize_true():
    """The zero-config path in dedupe_df must pass _skip_finalize=True to
    auto_configure_df so the controller does not run a full-data _finalize
    before the caller's own full pipeline run."""
    import goldenmatch as gm
    from goldenmatch.config.schemas import GoldenMatchConfig

    df = pl.DataFrame({"name": ["alice", "bob"] * 4, "city": ["x", "y"] * 4})
    with patch("goldenmatch._api.auto_configure_df") as mock_auto:
        mock_auto.return_value = GoldenMatchConfig(matchkeys=[])
        with patch("goldenmatch.core.pipeline.run_dedupe_df") as mock_pipeline:
            mock_pipeline.return_value = _trivial_dedupe_pipeline_return(df)
            gm.dedupe_df(df)
    call_kwargs = mock_auto.call_args.kwargs
    assert call_kwargs.get("_skip_finalize") is True, (
        f"Expected _skip_finalize=True to be forwarded; got kwargs={call_kwargs}"
    )


def test_zero_config_pipeline_called_auto_config_false():
    """After skip_finalize fix, run_dedupe_df must always be called with
    auto_config=False (never True) in the zero-config path."""
    import goldenmatch as gm
    from goldenmatch.config.schemas import GoldenMatchConfig

    df = pl.DataFrame({"name": ["alice", "bob"] * 4, "city": ["x", "y"] * 4})
    with patch("goldenmatch._api.auto_configure_df") as mock_auto:
        mock_auto.return_value = GoldenMatchConfig(matchkeys=[])
        with patch("goldenmatch.core.pipeline.run_dedupe_df") as mock_pipeline:
            mock_pipeline.return_value = _trivial_dedupe_pipeline_return(df)
            gm.dedupe_df(df)
    for call in mock_pipeline.call_args_list:
        assert call.kwargs.get("auto_config", False) is False
