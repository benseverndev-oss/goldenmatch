import pytest
from pydantic import ValidationError
from goldenmatch.config.schemas import GoldenFieldRule, GoldenGroupRule, GoldenRulesConfig


def test_field_rule_when_validate_optional():
    r = GoldenFieldRule(strategy="most_complete")
    assert r.when is None and r.validate_with is None
    r2 = GoldenFieldRule(strategy="most_recent", date_column="dt", when="state == 'CA'", validate="nanp")
    assert r2.when == "state == 'CA'" and r2.validate_with == "nanp"


def test_group_rule_validation():
    g = GoldenGroupRule(name="addr", columns=["street", "city"], strategy="most_complete")
    assert g.strategy == "most_complete"
    with pytest.raises(ValidationError):
        GoldenGroupRule(name="addr", columns=["only_one"])  # need >=2 columns
    with pytest.raises(ValidationError):
        GoldenGroupRule(name="addr", columns=["a", "b"], strategy="most_recent")  # needs date_column
    with pytest.raises(ValidationError):
        GoldenGroupRule(name="addr", columns=["a", "b"], strategy="majority_vote")  # not a group strategy


def test_group_strategy_allowlist():
    GoldenGroupRule(name="g", columns=["a", "b"], strategy="most_complete")
    GoldenGroupRule(name="g", columns=["a", "b"], strategy="most_recent", date_column="dt")
    GoldenGroupRule(name="g", columns=["a", "b"], strategy="source_priority", source_priority=["crm"])


def test_rules_config_field_groups_and_detection_default():
    c = GoldenRulesConfig(default_strategy="most_complete")
    assert c.field_groups == []
    assert c.field_group_detection is False


def test_field_rules_accepts_list_form():
    c = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"phone": [
            {"when": "state == 'CA'", "strategy": "most_recent", "date_column": "dt"},
            {"strategy": "source_priority", "source_priority": ["crm"]},
        ]},
    )
    assert isinstance(c.field_rules["phone"], list)


def test_overlapping_groups_rejected():
    with pytest.raises(ValidationError):
        GoldenRulesConfig(
            default_strategy="most_complete",
            field_groups=[
                {"name": "a", "columns": ["street", "city"]},
                {"name": "b", "columns": ["city", "zip"]},
            ],
        )


def test_group_column_also_in_field_rules_rejected():
    with pytest.raises(ValidationError):
        GoldenRulesConfig(
            default_strategy="most_complete",
            field_groups=[{"name": "a", "columns": ["street", "city"]}],
            field_rules={"street": {"strategy": "most_recent", "date_column": "dt"}},
        )


def test_default_clause_must_be_last():
    with pytest.raises(ValidationError):
        GoldenRulesConfig(
            default_strategy="most_complete",
            field_rules={"phone": [
                {"strategy": "source_priority", "source_priority": ["crm"]},
                {"when": "state == 'CA'", "strategy": "most_recent", "date_column": "dt"},
            ]},
        )


def test_list_form_requires_a_default_clause():
    with pytest.raises(ValidationError):
        GoldenRulesConfig(
            default_strategy="most_complete",
            field_rules={"phone": [
                {"when": "state == 'CA'", "strategy": "most_recent", "date_column": "dt"},
                {"when": "state == 'NY'", "strategy": "most_recent", "date_column": "dt"},
            ]},  # no when-less default clause
        )


def test_list_form_rejects_two_defaults():
    with pytest.raises(ValidationError):
        GoldenRulesConfig(
            default_strategy="most_complete",
            field_rules={"phone": [
                {"strategy": "source_priority", "source_priority": ["crm"]},
                {"strategy": "most_complete"},
            ]},  # two when-less default clauses
        )


def test_group_rejects_internal_prefixed_column():
    with pytest.raises(ValidationError):
        GoldenGroupRule(name="g", columns=["__source__", "city"])
