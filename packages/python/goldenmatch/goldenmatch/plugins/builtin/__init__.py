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

from goldenmatch.plugins.builtin.aggregation import (
    AgreementRateStrategy,
    CountDistinctStrategy,
    CountNonNullStrategy,
)
from goldenmatch.plugins.builtin.business import (
    EnumCanonicalStrategy,
    FreshnessWithMaxAgeStrategy,
    LifecycleStageStrategy,
    RegexValidatedStrategy,
    SystemOfRecordStrategy,
    WeightedByRecencyStrategy,
)
from goldenmatch.plugins.builtin.format import (
    BooleanNormalizeStrategy,
    ConcatUniqueStrategy,
    EmailNormalizeStrategy,
    PhoneDigitsOnlyStrategy,
    ShortestValueStrategy,
    UrlCanonicalStrategy,
    WhitespaceNormalizeStrategy,
)
from goldenmatch.plugins.builtin.numeric import (
    NumericMaxStrategy,
    NumericMeanStrategy,
    NumericMedianStrategy,
    NumericMinStrategy,
    NumericSumStrategy,
    NumericWeightedAverageStrategy,
)

# The full list of builtin plugin classes. Order is informational only;
# registration is by name (last-write-wins for entry-point overrides).
BUILTIN_PLUGINS = [
    # Numeric (6)
    NumericMaxStrategy,
    NumericMinStrategy,
    NumericMeanStrategy,
    NumericMedianStrategy,
    NumericSumStrategy,
    NumericWeightedAverageStrategy,
    # Format-canonical (7)
    ShortestValueStrategy,
    ConcatUniqueStrategy,
    EmailNormalizeStrategy,
    PhoneDigitsOnlyStrategy,
    UrlCanonicalStrategy,
    WhitespaceNormalizeStrategy,
    BooleanNormalizeStrategy,
    # Business-shaped (6)
    SystemOfRecordStrategy,
    LifecycleStageStrategy,
    FreshnessWithMaxAgeStrategy,
    EnumCanonicalStrategy,
    RegexValidatedStrategy,
    WeightedByRecencyStrategy,
    # Aggregation / telemetry (3)
    CountDistinctStrategy,
    CountNonNullStrategy,
    AgreementRateStrategy,
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
    "AgreementRateStrategy",
    "BooleanNormalizeStrategy",
    "ConcatUniqueStrategy",
    "CountDistinctStrategy",
    "CountNonNullStrategy",
    "EmailNormalizeStrategy",
    "EnumCanonicalStrategy",
    "FreshnessWithMaxAgeStrategy",
    "LifecycleStageStrategy",
    "NumericMaxStrategy",
    "NumericMeanStrategy",
    "NumericMedianStrategy",
    "NumericMinStrategy",
    "NumericSumStrategy",
    "NumericWeightedAverageStrategy",
    "PhoneDigitsOnlyStrategy",
    "RegexValidatedStrategy",
    "ShortestValueStrategy",
    "SystemOfRecordStrategy",
    "UrlCanonicalStrategy",
    "WeightedByRecencyStrategy",
    "WhitespaceNormalizeStrategy",
    "register_builtins",
]
