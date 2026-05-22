"""Predefined golden-strategy plugins (v1.18.2, #predefined-merge-plugins).

Three categories of plugin shipping with goldenmatch:

- numeric: numeric_max, numeric_min, numeric_mean
- format:  shortest_value, concat_unique, email_normalize, phone_digits_only
- business: system_of_record, lifecycle_stage, freshness_with_max_age

Used via ``strategy="custom:<name>"`` in GoldenRulesConfig.field_rules.
Auto-registered by ``PluginRegistry.discover()``; user entry-point
plugins with the same name win (last-registration-wins in
``_register()``).

Spec: ``docs/superpowers/specs/2026-05-22-predefined-merge-plugins-design.md``
"""
from __future__ import annotations

from goldenmatch.plugins.builtin.business import (
    FreshnessWithMaxAgeStrategy,
    LifecycleStageStrategy,
    SystemOfRecordStrategy,
)
from goldenmatch.plugins.builtin.format import (
    ConcatUniqueStrategy,
    EmailNormalizeStrategy,
    PhoneDigitsOnlyStrategy,
    ShortestValueStrategy,
)
from goldenmatch.plugins.builtin.numeric import (
    NumericMaxStrategy,
    NumericMeanStrategy,
    NumericMinStrategy,
)

# The full list of builtin plugin classes. Order is informational only;
# registration is by name (last-write-wins for entry-point overrides).
BUILTIN_PLUGINS = [
    # Numeric (3)
    NumericMaxStrategy,
    NumericMinStrategy,
    NumericMeanStrategy,
    # Format-canonical (4)
    ShortestValueStrategy,
    ConcatUniqueStrategy,
    EmailNormalizeStrategy,
    PhoneDigitsOnlyStrategy,
    # Business-shaped (3)
    SystemOfRecordStrategy,
    LifecycleStageStrategy,
    FreshnessWithMaxAgeStrategy,
]


def register_builtins(registry: object) -> None:
    """Register every builtin plugin on the given PluginRegistry.

    Called from ``PluginRegistry.discover()`` BEFORE entry-point scan
    so that user entry-point plugins with the same name override
    the builtin (consistent with the documented "user wins" pattern).
    """
    for cls in BUILTIN_PLUGINS:
        plugin = cls()
        # Bypass the public register_golden_strategy() type check --
        # builtins don't need protocol validation since they're shipped
        # with the package and verified via the test suite.
        registry._register("golden_strategy", plugin.name, plugin)  # type: ignore[attr-defined]


__all__ = [
    "BUILTIN_PLUGINS",
    "ConcatUniqueStrategy",
    "EmailNormalizeStrategy",
    "FreshnessWithMaxAgeStrategy",
    "LifecycleStageStrategy",
    "NumericMaxStrategy",
    "NumericMeanStrategy",
    "NumericMinStrategy",
    "PhoneDigitsOnlyStrategy",
    "ShortestValueStrategy",
    "SystemOfRecordStrategy",
    "register_builtins",
]
