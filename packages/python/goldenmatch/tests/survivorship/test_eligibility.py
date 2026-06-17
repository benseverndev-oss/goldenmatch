"""E4 tests: _survivorship_active + _polars_native_eligible gate."""
from goldenmatch.config.schemas import GoldenFieldRule, GoldenGroupRule, GoldenRulesConfig
from goldenmatch.core.golden import _polars_native_eligible, _survivorship_active


def test_field_groups_force_slow_path():
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="a", columns=["x", "y"])],
    )
    assert _polars_native_eligible(rules, None) is False
    assert _survivorship_active(rules) is True


def test_conditional_field_rule_forces_slow_path():
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={
            "p": [
                GoldenFieldRule(strategy="most_complete", when="x == 1"),
                GoldenFieldRule(strategy="most_complete"),
            ]
        },
    )
    assert _polars_native_eligible(rules, None) is False
    assert _survivorship_active(rules) is True


def test_validate_forces_slow_path():
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"p": GoldenFieldRule(strategy="most_complete", validate="nanp")},
    )
    assert _survivorship_active(rules) is True


def test_plain_config_unaffected():
    rules = GoldenRulesConfig(default_strategy="most_complete")
    assert _polars_native_eligible(rules, None) is True
    assert _survivorship_active(rules) is False
