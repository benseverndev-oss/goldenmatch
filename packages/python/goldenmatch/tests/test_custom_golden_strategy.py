"""Tests for custom golden-strategy plugin slot (v1.18.1).

Spec: docs/superpowers/specs/2026-05-22-golden-strategy-plugin-slot-design.md
"""

from __future__ import annotations

from typing import Any

import pytest
from goldenmatch.config.schemas import GoldenFieldRule
from goldenmatch.core.golden import merge_field
from goldenmatch.plugins.registry import PluginRegistry


@pytest.fixture(autouse=True)
def _reset_registry():
    """Each test gets a clean registry so cross-test contamination is impossible."""
    PluginRegistry.reset()
    yield
    PluginRegistry.reset()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_custom_strategy_name_passes_validation():
    """`custom:foo` is a valid GoldenFieldRule strategy."""
    rule = GoldenFieldRule(strategy="custom:legal_priority")
    assert rule.strategy == "custom:legal_priority"


def test_invalid_custom_name_rejected_by_validator():
    """Empty / hyphenated / uppercase names fail validation."""
    with pytest.raises(ValueError):
        GoldenFieldRule(strategy="custom:")
    with pytest.raises(ValueError):
        GoldenFieldRule(strategy="custom:has-hyphen")
    with pytest.raises(ValueError):
        GoldenFieldRule(strategy="custom:HasCaps")


def test_custom_strategy_validator_does_not_require_plugin_to_exist():
    """Plugin existence is checked at DISPATCH time, not validate.
    Loading a config before plugins are discovered must succeed."""
    # Plugin registry is empty (autouse fixture reset); rule still validates.
    rule = GoldenFieldRule(strategy="custom:nonexistent_yet")
    assert rule.strategy == "custom:nonexistent_yet"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


class _SimpleStrategy:
    """Returns the first value, confidence 0.42. Used to verify dispatch."""

    name = "simple"

    def merge(self, values, **kwargs) -> Any:
        return (values[0], 0.42)


class _RichStrategy:
    """Returns a 3-tuple with idx=last_index."""

    name = "rich"

    def merge(self, values, **kwargs) -> Any:
        return (values[-1], 0.91, len(values) - 1)


class _IntrospectingStrategy:
    """Captures kwargs it receives so the test can assert on them."""

    name = "introspect"

    def __init__(self):
        self.last_kwargs: dict = {}

    def merge(self, values, **kwargs) -> Any:
        self.last_kwargs = dict(kwargs)
        return (values[0], 1.0)


class _RaisingStrategy:
    """Always raises on merge."""

    name = "raises"

    def merge(self, values, **kwargs) -> Any:
        raise RuntimeError("plugin error")


def test_dispatch_calls_plugin_with_kwargs():
    """Dispatcher passes values + sources + dates + quality_weights +
    pair_scores + rule_kwargs to the plugin."""
    plugin = _IntrospectingStrategy()
    PluginRegistry.instance().register_golden_strategy("introspect", plugin)
    rule = GoldenFieldRule(
        strategy="custom:introspect",
        source_priority=["a", "b"],
    )
    merge_field(
        values=["x", "y"],
        rule=rule,
        sources=["a", "b"],
        dates=[1, 2],
        quality_weights=[0.5, 0.7],
        pair_scores={(0, 1): 0.9},
    )
    assert plugin.last_kwargs["sources"] == ["a", "b"]
    assert plugin.last_kwargs["dates"] == [1, 2]
    assert plugin.last_kwargs["quality_weights"] == [0.5, 0.7]
    assert plugin.last_kwargs["pair_scores"] == {(0, 1): 0.9}
    # rule_kwargs carries the GoldenFieldRule fields (excluding strategy).
    assert plugin.last_kwargs["rule_kwargs"]["source_priority"] == ["a", "b"]


def test_dispatch_accepts_two_tuple_result():
    """Plugin returning (value, conf) -> dispatcher synthesizes idx=0."""
    PluginRegistry.instance().register_golden_strategy("simple", _SimpleStrategy())
    rule = GoldenFieldRule(strategy="custom:simple")
    winner, conf, idx = merge_field(values=["x", "y", "z"], rule=rule)
    assert winner == "x"
    assert conf == 0.42
    assert idx == 0


def test_dispatch_accepts_three_tuple_result():
    """Plugin returning (value, conf, idx) -> idx preserved."""
    PluginRegistry.instance().register_golden_strategy("rich", _RichStrategy())
    rule = GoldenFieldRule(strategy="custom:rich")
    winner, conf, idx = merge_field(values=["x", "y", "z"], rule=rule)
    assert winner == "z"
    assert conf == 0.91
    assert idx == 2


def test_dispatch_falls_back_on_missing_plugin(caplog: pytest.LogCaptureFixture):
    """Unknown plugin name -> WARNING + most_complete fallback."""
    import logging

    rule = GoldenFieldRule(strategy="custom:nonexistent")
    with caplog.at_level(logging.WARNING, logger="goldenmatch.core.golden"):
        winner, _conf, _idx = merge_field(
            values=["short", "much longer string"],
            rule=rule,
        )
    # most_complete picks the longer string (its proxy for completeness).
    assert winner == "much longer string"
    warnings = [r.getMessage() for r in caplog.records if "nonexistent" in r.getMessage()]
    assert warnings, "no warning logged for missing plugin"


def test_dispatch_falls_back_on_plugin_exception(caplog: pytest.LogCaptureFixture):
    """Plugin raises -> WARNING + most_complete fallback."""
    import logging

    PluginRegistry.instance().register_golden_strategy("raises", _RaisingStrategy())
    rule = GoldenFieldRule(strategy="custom:raises")
    with caplog.at_level(logging.WARNING, logger="goldenmatch.core.golden"):
        winner, _conf, _idx = merge_field(
            values=["a", "much longer"],
            rule=rule,
        )
    assert winner == "much longer"  # most_complete picked longer
    warnings = [r.getMessage() for r in caplog.records if "raises" in r.getMessage()]
    assert warnings


def test_strict_mode_reraises_plugin_exception(monkeypatch: pytest.MonkeyPatch):
    """GOLDENMATCH_GOLDEN_STRATEGY_STRICT=1 -> exception bubbles."""
    monkeypatch.setenv("GOLDENMATCH_GOLDEN_STRATEGY_STRICT", "1")
    PluginRegistry.instance().register_golden_strategy("raises", _RaisingStrategy())
    rule = GoldenFieldRule(strategy="custom:raises")
    with pytest.raises(RuntimeError, match="plugin error"):
        merge_field(values=["x", "y"], rule=rule)


def test_strict_mode_raises_on_missing_plugin(monkeypatch: pytest.MonkeyPatch):
    """Strict mode: missing plugin -> ValueError instead of fallback."""
    monkeypatch.setenv("GOLDENMATCH_GOLDEN_STRATEGY_STRICT", "1")
    rule = GoldenFieldRule(strategy="custom:nonexistent")
    with pytest.raises(ValueError, match="not found"):
        merge_field(values=["x", "y"], rule=rule)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
