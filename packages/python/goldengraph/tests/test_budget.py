"""Slice 4c budget seam -- wheel-free Budget + _BudgetedLLM (the cross-controller ceiling)."""
from __future__ import annotations

import pytest
from goldengraph.budget import Budget, BudgetExhausted, _BudgetedLLM


class _StubLLM:
    def __init__(self):
        self.calls = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        return "ok response text"


def test_unbounded_budget_is_passthrough():
    b = Budget()
    assert b.would_exceed(10**9) is False
    bllm = _BudgetedLLM(_StubLLM(), b)
    assert bllm.complete("hello there") == "ok response text"
    assert b.spent_tokens > 0  # recorded


def test_one_budget_shared_across_two_draws():
    # the cross-controller proof: a "build" draw then an "ask" draw on ONE budget
    b = Budget(total_tokens=10_000)
    bllm = _BudgetedLLM(_StubLLM(), b)
    bllm.complete("x" * 80)          # build draw
    after_build = b.spent_tokens
    assert after_build > 0
    bllm.complete("y" * 80)          # ask draw, SAME pool
    assert b.spent_tokens > after_build


def test_over_budget_input_raises_before_calling():
    b = Budget(total_tokens=5)
    stub = _StubLLM()
    bllm = _BudgetedLLM(stub, b)
    with pytest.raises(BudgetExhausted):
        bllm.complete("z" * 400)     # est_in = 100 > 5
    assert stub.calls == 0           # raised BEFORE delegating


class _JsonStub:
    def __init__(self):
        self.calls = []

    def complete(self, prompt: str) -> str:
        self.calls.append("complete")
        return "x"

    def complete_json(self, prompt: str) -> str:
        self.calls.append("complete_json")
        return "y"


def test_budgeted_complete_json_forwards_and_charges():
    b = Budget(total_tokens=10_000)
    inner = _JsonStub()
    bllm = _BudgetedLLM(inner, b)
    assert bllm.complete_json("hello there") == "y"
    assert inner.calls == ["complete_json"]
    assert b.spent_tokens > 0


def test_budgeted_complete_json_falls_back_without_inner():
    b = Budget()
    inner = _StubLLM()  # has complete only
    bllm = _BudgetedLLM(inner, b)
    assert bllm.complete_json("hi") == "ok response text"  # routed to inner.complete


def test_exhaustion_on_next_call_not_exact_ceiling():
    # spent may overshoot total (output not pre-charged); contract = NEXT over-input call raises
    b = Budget(total_tokens=30)
    bllm = _BudgetedLLM(_StubLLM(), b)
    bllm.complete("a" * 40)          # est_in 10 <= 30, passes; records 10 + out//4
    with pytest.raises(BudgetExhausted):
        bllm.complete("b" * 200)     # est_in 50 > remaining
