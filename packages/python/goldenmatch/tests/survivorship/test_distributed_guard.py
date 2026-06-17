"""Tests for spec 4.4: distributed survivorship guards (H1+H2).

Both the Ray phase-5 streaming pipeline and the Sail backend must REFUSE
(NotImplementedError) when correlated survivorship is active rather than
silently applying a plain most_complete merge that would produce wrong
golden records.
"""
import pytest
from goldenmatch.config.schemas import GoldenFieldRule, GoldenGroupRule, GoldenRulesConfig
from goldenmatch.core.golden import assert_in_memory_survivorship

# ---------------------------------------------------------------------------
# Helper tests (shared guard function)
# ---------------------------------------------------------------------------

def test_helper_raises_on_field_groups():
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="a", columns=["x", "y"])],
    )
    with pytest.raises(NotImplementedError):
        assert_in_memory_survivorship(rules, "test path")


def test_helper_raises_on_conditional():
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={
            "p": [
                GoldenFieldRule(strategy="most_complete", when="x == 1"),
                GoldenFieldRule(strategy="most_complete"),
            ]
        },
    )
    with pytest.raises(NotImplementedError):
        assert_in_memory_survivorship(rules, "test path")


def test_helper_noop_when_inactive():
    rules = GoldenRulesConfig(default_strategy="most_complete")
    assert_in_memory_survivorship(rules, "test path")  # no raise


def test_helper_noop_when_none():
    assert_in_memory_survivorship(None, "test path")  # no raise


def test_helper_error_message_mentions_where():
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="g", columns=["a", "b"])],
    )
    with pytest.raises(NotImplementedError, match="my custom path"):
        assert_in_memory_survivorship(rules, "my custom path")


# ---------------------------------------------------------------------------
# Sail build_golden call-site test (H2)
#
# build_golden now accepts an optional `rules` kwarg and calls
# assert_in_memory_survivorship before any PySpark import, so it can be
# tested without a live Sail session. Pass dummy Spark-DataFrame-shaped
# objects (None) -- they are never reached if the guard fires first.
# ---------------------------------------------------------------------------

def test_sail_build_golden_refuses_field_groups():
    from goldenmatch.sail.golden import build_golden

    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="name", columns=["first", "last"])],
    )
    with pytest.raises(NotImplementedError, match="Sail distributed backend"):
        build_golden(
            None,  # assignments_df -- never reached
            None,  # source_df -- never reached
            value_cols=["first", "last"],
            rules=rules,
        )


def test_sail_build_golden_refuses_conditional():
    from goldenmatch.sail.golden import build_golden

    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={
            "email": [
                GoldenFieldRule(strategy="most_complete", when="verified == 1"),
                GoldenFieldRule(strategy="most_complete"),
            ]
        },
    )
    with pytest.raises(NotImplementedError, match="Sail distributed backend"):
        build_golden(
            None,
            None,
            value_cols=["email"],
            rules=rules,
        )


def test_sail_build_golden_noop_without_survivorship():
    """No rules passed -> no survivorship -> no guard raise.

    The function proceeds past the guard and then fails on the real PySpark
    import / None args, which is expected (we only care that the guard does
    NOT fire here).
    """
    from goldenmatch.sail.golden import build_golden

    # Without rules=, the guard is a no-op. The function then tries to use
    # PySpark which isn't available in CI, raising ImportError or AttributeError
    # -- NOT NotImplementedError.
    with pytest.raises(Exception) as exc_info:
        build_golden(
            None,
            None,
            value_cols=["x"],
        )
    assert not isinstance(exc_info.value, NotImplementedError), (
        "guard should NOT fire when survivorship is inactive"
    )


# ---------------------------------------------------------------------------
# Ray phase-5 pipeline call-site test (H1)
#
# _run_phase5_pipeline requires Ray and calls auto_configure_df (which also
# needs a Dataset). The guard fires after cfg is resolved -- which means it
# fires after auto_configure_df or config is set. With an explicit config
# containing survivorship, we can mock auto_configure_df out AND the Dataset
# parameter to reach the guard with no Ray runtime.
# ---------------------------------------------------------------------------

def test_phase5_pipeline_refuses_survivorship(monkeypatch):
    """The guard in _run_phase5_pipeline fires when an explicit survivorship
    config is passed, before any Ray work begins."""
    import goldenmatch.distributed.pipeline as _pipeline_mod

    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="grp", columns=["a", "b"])],
    )

    class _FakeConfig:
        golden_rules = rules

    # Patch auto_configure_df so the no-config path is also safe to call
    # (in case config=None path is taken). With config=_FakeConfig(), the
    # auto_configure_df branch is skipped anyway.
    monkeypatch.setattr(
        "goldenmatch.distributed.pipeline.auto_configure_df",
        lambda *a, **kw: _FakeConfig(),
        raising=False,
    )

    with pytest.raises(NotImplementedError, match="distributed streaming pipeline"):
        _pipeline_mod._run_phase5_pipeline(
            None,  # ds -- never reached past the guard
            config=_FakeConfig(),
        )


def test_helper_raises_on_validate():
    rules = GoldenRulesConfig(default_strategy="most_complete",
                              field_rules={"phone": GoldenFieldRule(strategy="most_complete", validate="nanp")})
    with pytest.raises(NotImplementedError):
        assert_in_memory_survivorship(rules, "test path")
