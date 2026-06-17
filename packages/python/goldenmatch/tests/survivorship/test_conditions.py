import pytest
from goldenmatch.core.survivorship.conditions import eval_predicate, PredicateError


@pytest.mark.parametrize("expr,resolved,expected", [
    ("state == 'CA'", {"state": "CA"}, True),
    ("state == 'CA'", {"state": "NY"}, False),
    ("state in ['CA', 'NY']", {"state": "NY"}, True),
    ("state not in ['CA', 'NY']", {"state": "TX"}, True),
    ("age > 30", {"age": 40}, True),
    ("age > -1", {"age": 0}, True),
    ("not flagged", {"flagged": False}, True),
    ("a == 1 and b == 2", {"a": 1, "b": 2}, True),
    ("a == 1 or b == 2", {"a": 1, "b": 9}, True),
])
def test_allowed_predicates(expr, resolved, expected):
    assert eval_predicate(expr, resolved) is expected


def test_unknown_name_is_miss():
    assert eval_predicate("missing == 'x'", {}) is False


def test_none_operand_is_miss_not_error():
    assert eval_predicate("state == 'CA'", {"state": None}) is False
    assert eval_predicate("age > 30", {"age": None}) is False
    assert eval_predicate("state in ['CA']", {"state": None}) is False


@pytest.mark.parametrize("expr", [
    "__import__('os').system('x')",
    "obj.attr == 1",
    "func()",
    "a + b == 2",
    "[x for x in y]",
])
def test_dangerous_expressions_rejected(expr):
    with pytest.raises(PredicateError):
        eval_predicate(expr, {"a": 1, "b": 1, "y": []})


from goldenmatch.config.schemas import GoldenFieldRule, GoldenGroupRule
from goldenmatch.core.survivorship.conditions import (
    select_conditional_strategy, build_resolution_order, ResolutionError,
)


def test_select_conditional_first_match_wins():
    rules = [
        GoldenFieldRule(strategy="most_recent", date_column="dt", when="state == 'CA'"),
        GoldenFieldRule(strategy="source_priority", source_priority=["crm"]),
    ]
    assert select_conditional_strategy(rules, {"state": "CA"}).strategy == "most_recent"
    assert select_conditional_strategy(rules, {"state": "TX"}).strategy == "source_priority"


def test_resolution_order_respects_when_deps():
    field_rules = {
        "state": GoldenFieldRule(strategy="most_complete"),
        "phone": [
            GoldenFieldRule(strategy="most_recent", date_column="dt", when="state == 'CA'"),
            GoldenFieldRule(strategy="source_priority", source_priority=["crm"]),
        ],
    }
    order = build_resolution_order(field_rules, groups=[], all_columns=["state", "phone", "dt"])
    assert order.index("state") < order.index("phone")


def test_resolution_order_when_references_group_member():
    groups = [GoldenGroupRule(name="addr", columns=["street", "state"])]
    field_rules = {
        "phone": [
            GoldenFieldRule(strategy="most_recent", date_column="dt", when="state == 'CA'"),
            GoldenFieldRule(strategy="source_priority", source_priority=["crm"]),
        ],
    }
    order = build_resolution_order(field_rules, groups=groups, all_columns=["street", "state", "phone", "dt"])
    assert order.index("group:addr") < order.index("phone")


def test_circular_when_rejected():
    field_rules = {
        "a": [GoldenFieldRule(strategy="most_complete", when="b == 1"),
              GoldenFieldRule(strategy="most_complete")],
        "b": [GoldenFieldRule(strategy="most_complete", when="a == 1"),
              GoldenFieldRule(strategy="most_complete")],
    }
    with pytest.raises(ResolutionError):
        build_resolution_order(field_rules, groups=[], all_columns=["a", "b"])


def test_unary_on_non_numeric_is_miss_not_error():
    # Previously raised an uncaught TypeError; now a miss -> False.
    assert eval_predicate("-x > 1", {"x": "hello"}) is False


def test_or_succeeds_when_one_arm_references_absent_field():
    assert eval_predicate("missing == 1 or y == 1", {"y": 1}) is True


def test_and_with_missing_arm_is_false():
    assert eval_predicate("missing == 1 and y == 1", {"y": 1}) is False
