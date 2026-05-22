"""Tests for builtin-plugin discovery (#predefined-merge-plugins).

PluginRegistry.discover() auto-registers the 10 builtins BEFORE
entry-point scan so user entry-point plugins override builtins by
last-write-wins.
"""
from __future__ import annotations

import pytest
from goldenmatch.plugins.builtin import BUILTIN_PLUGINS
from goldenmatch.plugins.registry import PluginRegistry

EXPECTED_BUILTIN_NAMES = {
    # Numeric (6)
    "numeric_max", "numeric_min", "numeric_mean",
    "numeric_median", "numeric_sum", "numeric_weighted_average",
    # Format-canonical (7)
    "shortest_value", "concat_unique", "email_normalize", "phone_digits_only",
    "url_canonical", "whitespace_normalize", "boolean_normalize",
    # Business (6)
    "system_of_record", "lifecycle_stage", "freshness_with_max_age",
    "enum_canonical", "regex_validated", "weighted_by_recency",
    # Aggregation / telemetry (3)
    "count_distinct", "count_non_null", "agreement_rate",
}


@pytest.fixture(autouse=True)
def _reset_registry():
    PluginRegistry.reset()
    yield
    PluginRegistry.reset()


def test_builtins_list_matches_expected():
    """The BUILTIN_PLUGINS list contains exactly the 10 documented names."""
    actual_names = {cls().name for cls in BUILTIN_PLUGINS}
    assert actual_names == EXPECTED_BUILTIN_NAMES


def test_discover_registers_all_builtins():
    """After PluginRegistry.discover(), each builtin name is fetchable."""
    registry = PluginRegistry.instance()
    registry.discover()
    for name in EXPECTED_BUILTIN_NAMES:
        plugin = registry.get_golden_strategy(name)
        assert plugin is not None, f"builtin {name!r} not registered"
        assert plugin.name == name  # type: ignore[attr-defined]


def test_list_plugins_includes_builtins():
    """`list_plugins()` reflects auto-registration."""
    registry = PluginRegistry.instance()
    registry.discover()
    plugins = registry.list_plugins()
    listed = set(plugins["golden_strategy"])
    assert EXPECTED_BUILTIN_NAMES.issubset(listed)


def test_user_plugin_can_override_builtin_by_name():
    """A user-registered plugin with the same name as a builtin wins."""
    registry = PluginRegistry.instance()
    registry.discover()  # builtins now registered

    class _CustomOverride:
        name = "numeric_max"
        def merge(self, values, **kwargs):
            return ("overridden", 1.0)

    registry.register_golden_strategy("numeric_max", _CustomOverride())
    fetched = registry.get_golden_strategy("numeric_max")
    assert fetched is not None
    assert fetched.__class__.__name__ == "_CustomOverride"


def test_dispatch_works_end_to_end_via_custom_strategy_prefix():
    """`strategy="custom:numeric_max"` dispatches to the builtin."""
    from goldenmatch.config.schemas import GoldenFieldRule
    from goldenmatch.core.golden import merge_field

    rule = GoldenFieldRule(strategy="custom:numeric_max")
    val, _conf, _idx = merge_field([10, 50, 25], rule)
    assert val == 50


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
