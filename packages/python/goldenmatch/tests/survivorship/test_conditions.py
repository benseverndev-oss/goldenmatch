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
