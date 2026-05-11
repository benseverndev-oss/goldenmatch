from unittest.mock import patch

import goldenmatch
import polars as pl
import pytest
from goldenmatch.config.schemas import GoldenMatchConfig


def test_auto_configure_df_facade_returns_goldenmatchconfig():
    """Public signature unchanged; returned object is GoldenMatchConfig."""
    df = pl.DataFrame({
        "name": ["alice", "alyce", "bob", "bobby", "carol"] * 4,
        "city": ["nyc", "la", "sf", "nyc", "la"] * 4,
    })
    cfg = goldenmatch.auto_configure_df(df)
    assert isinstance(cfg, GoldenMatchConfig)


def test_auto_configure_df_invokes_controller_run():
    """auto_configure_df dispatches to AutoConfigController.run."""
    df = pl.DataFrame({
        "name": ["alice", "alyce"] * 50,
        "city": ["nyc"] * 100,
    })
    with patch("goldenmatch.core.autoconfig_controller.AutoConfigController.run") as mock_run:
        from goldenmatch.core.autoconfig_history import RunHistory
        from goldenmatch.core.complexity_profile import ComplexityProfile
        mock_run.return_value = (
            GoldenMatchConfig(matchkeys=[]),
            ComplexityProfile(),
            RunHistory(),
        )
        goldenmatch.auto_configure_df(df)
    mock_run.assert_called_once()


def test_auto_configure_df_match_mode_with_reference():
    """New: reference kwarg triggers match-mode auto-config."""
    target = pl.DataFrame({
        "id": ["1", "2", "3", "4", "5"] * 4,
        "title": ["foo paper", "bar work", "baz study", "qux note", "doc"] * 4,
    })
    ref = pl.DataFrame({
        "id": ["10", "20", "30", "40", "50"] * 4,
        "title": ["foo paper", "bar work", "different", "fourth", "fifth"] * 4,
    })
    cfg = goldenmatch.auto_configure_df(target, reference=ref)
    assert isinstance(cfg, GoldenMatchConfig)
    assert len(cfg.matchkeys) >= 1


def test_auto_configure_df_lazyframe_collected():
    """LazyFrame inputs are accepted (collected internally)."""
    lf = pl.DataFrame({
        "name": ["a", "b", "c"] * 4,
        "city": ["x", "y", "z"] * 4,
    }).lazy()
    cfg = goldenmatch.auto_configure_df(lf)
    assert isinstance(cfg, GoldenMatchConfig)


def test_auto_configure_df_lazyframe_reference_collected():
    """LazyFrame reference is also accepted."""
    target = pl.DataFrame({
        "id": ["1", "2"] * 5,
        "title": ["foo", "bar"] * 5,
    })
    ref = pl.DataFrame({
        "id": ["10", "20"] * 5,
        "title": ["foo", "baz"] * 5,
    }).lazy()
    cfg = goldenmatch.auto_configure_df(target, reference=ref)
    assert isinstance(cfg, GoldenMatchConfig)


def test_auto_configure_df_rejects_non_dataframe():
    with pytest.raises(TypeError, match=r"DataFrame"):
        goldenmatch.auto_configure_df([{"name": "alice"}])  # list of dicts


def test_auto_configure_df_rejects_non_dataframe_reference():
    target = pl.DataFrame({"name": ["a", "b"]})
    with pytest.raises(TypeError, match=r"DataFrame"):
        goldenmatch.auto_configure_df(target, reference={"name": "x"})


def test_last_controller_run_contextvar_populated():
    """After auto_configure_df, the _LAST_CONTROLLER_RUN ContextVar holds the
    (profile, history) tuple from the last run."""
    from goldenmatch.core.autoconfig import _LAST_CONTROLLER_RUN
    df = pl.DataFrame({
        "name": ["a", "b", "c"] * 4,
        "city": ["x", "y", "z"] * 4,
    })
    goldenmatch.auto_configure_df(df)
    state = _LAST_CONTROLLER_RUN.get()
    assert state is not None
    profile, history = state
    from goldenmatch.core.autoconfig_history import RunHistory
    from goldenmatch.core.complexity_profile import ComplexityProfile
    assert isinstance(profile, ComplexityProfile)
    assert isinstance(history, RunHistory)


def test_legacy_auto_configure_v0_still_callable():
    """The private _legacy_auto_configure_v0 must remain importable for
    AutoConfigController._initial_config (would loop otherwise)."""
    from goldenmatch.core.autoconfig import _legacy_auto_configure_v0
    df = pl.DataFrame({
        "name": ["alice", "bob"] * 5,
        "city": ["nyc", "la"] * 5,
    })
    cfg = _legacy_auto_configure_v0(df)
    assert isinstance(cfg, GoldenMatchConfig)


# ============================================================
# Fix 3 — kwargs threaded through to _legacy_auto_configure_v0
# ============================================================

def test_kwargs_threaded_to_v0():
    """auto_configure_df forwards strict/llm_auto kwargs into _legacy_auto_configure_v0."""
    from unittest.mock import patch as _patch


    df = pl.DataFrame({"name": ["a", "b"] * 5, "city": ["x", "y"] * 5})
    with _patch("goldenmatch.core.autoconfig._legacy_auto_configure_v0") as mock_v0:
        mock_v0.return_value = GoldenMatchConfig(matchkeys=[])
        goldenmatch.auto_configure_df(df, strict=True, llm_auto=True)
    mock_v0.assert_called()
    call_kwargs = mock_v0.call_args.kwargs
    assert call_kwargs.get("strict") is True, (
        f"expected strict=True to be forwarded; got kwargs={call_kwargs}"
    )
    assert call_kwargs.get("llm_auto") is True, (
        f"expected llm_auto=True to be forwarded; got kwargs={call_kwargs}"
    )
