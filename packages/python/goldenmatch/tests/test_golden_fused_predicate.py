"""Unit tests for the Stage-6 conditional-predicate lowering.

Covers ``predicate_lowerable`` (which config predicates the gate accepts) and
``lower_predicate`` (the RPN IR emitted for the kernel). The IR-eval byte-parity
against ``conditions.eval_predicate`` is exercised end-to-end by the
``run_golden_fused_arrow`` conditional parity tests in ``test_golden_fused.py``.
"""

from __future__ import annotations

from goldenmatch.core.golden_fused_predicate import (
    _ABSENT_CODE,
    OP_AND,
    OP_EQ,
    OP_IN,
    OP_MISS,
    OP_NE,
    OP_NOT,
    OP_OR,
    lower_predicate,
    predicate_lowerable,
)

# ── predicate_lowerable ──────────────────────────────────────────────────────


def test_lowerable_eq_string():
    assert predicate_lowerable('country == "US"') is True


def test_lowerable_in_list():
    assert predicate_lowerable('state in ["NY", "NJ"]') is True


def test_lowerable_not_in_list():
    assert predicate_lowerable('state not in ["NY", "NJ"]') is True


def test_lowerable_and_ne():
    assert predicate_lowerable('a == 1 and b != 2') is True


def test_lowerable_or_and_not():
    assert predicate_lowerable('not (x == "z") or y == 3') is True


def test_lowerable_eq_none():
    assert predicate_lowerable('country == None') is True


def test_not_lowerable_function_call():
    assert predicate_lowerable('len(country) == 2') is False


def test_not_lowerable_attribute_access():
    # attribute access is rejected by the conditions.py allowlist -> not lowerable
    assert predicate_lowerable('country.upper == "US"') is False


def test_not_lowerable_ordering_lt():
    assert predicate_lowerable('score < 5') is False


def test_not_lowerable_ordering_ge():
    assert predicate_lowerable('score >= 5') is False


def test_not_lowerable_chained_comparison():
    assert predicate_lowerable('1 < x < 5') is False


def test_not_lowerable_reversed_operands():
    # literal on the left is not the `Name <op> literal` shape we lower
    assert predicate_lowerable('"US" == country') is False


def test_not_lowerable_bare_name():
    assert predicate_lowerable('is_primary') is False


def test_not_lowerable_non_string():
    assert predicate_lowerable(None) is False
    assert predicate_lowerable(123) is False


def test_not_lowerable_syntax_error():
    assert predicate_lowerable('country ==') is False


# ── lower_predicate (IR shape) ───────────────────────────────────────────────


def _code_of_factory():
    # country: US=5 present, CA=3 present; state: NY=1, NJ=2 present.
    tables = {
        "country": {"US": 5, "CA": 3},
        "state": {"NY": 1, "NJ": 2},
        "a": {1: 0},
        "b": {2: 7},
    }

    def code_of(name, lit):
        if lit is None:
            return -1
        return tables.get(name, {}).get(lit, _ABSENT_CODE)

    return code_of


def test_lower_eq_present_literal():
    col_index = {"country": 0}
    ir = lower_predicate('country == "US"', col_index, _code_of_factory())
    assert len(ir) == 1
    assert ir[0].op == OP_EQ
    assert ir[0].col_index == 0
    assert ir[0].codes == [5]


def test_lower_eq_absent_literal_uses_sentinel():
    col_index = {"country": 0}
    ir = lower_predicate('country == "ZZ"', col_index, _code_of_factory())
    assert ir[0].op == OP_EQ
    assert ir[0].codes == [_ABSENT_CODE]


def test_lower_eq_none_uses_null_code():
    col_index = {"country": 0}
    ir = lower_predicate('country == None', col_index, _code_of_factory())
    assert ir[0].op == OP_EQ
    assert ir[0].codes == [-1]


def test_lower_in_list_codes():
    col_index = {"state": 1}
    ir = lower_predicate('state in ["NY", "NJ", "ZZ"]', col_index, _code_of_factory())
    assert ir[0].op == OP_IN
    assert ir[0].col_index == 1
    assert ir[0].codes == [1, 2, _ABSENT_CODE]


def test_lower_and_rpn_order():
    col_index = {"a": 0, "b": 1}
    ir = lower_predicate('a == 1 and b != 2', col_index, _code_of_factory())
    # RPN: [EQ a, NE b, AND(arity=2)]
    assert [i.op for i in ir] == [OP_EQ, OP_NE, OP_AND]
    assert ir[0].codes == [0] and ir[0].col_index == 0
    assert ir[1].codes == [7] and ir[1].col_index == 1
    assert ir[2].arity == 2


def test_lower_not_or_rpn():
    col_index = {"x": 0, "y": 1}
    # y not in tables -> code_of returns absent for its literal; x present.
    ir = lower_predicate('not (x == "US") or y == "NY"', col_index, _code_of_factory())
    # RPN: [EQ x, NOT, EQ y, OR(arity=2)]
    assert [i.op for i in ir] == [OP_EQ, OP_NOT, OP_EQ, OP_OR]
    assert ir[3].arity == 2


def test_lower_unknown_name_emits_miss():
    # `region` is not a resolvable column -> a single MISS instruction.
    ir = lower_predicate('region == "west"', {"country": 0}, _code_of_factory())
    assert len(ir) == 1
    assert ir[0].op == OP_MISS
