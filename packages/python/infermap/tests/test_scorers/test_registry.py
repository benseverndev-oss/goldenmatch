"""Tests for scorer registry and helpers."""
from __future__ import annotations

from infermap.scorers import default_scorers, scorer, _REGISTRY, ExactScorer, AliasScorer
from infermap.scorers.base import Scorer
from infermap.types import FieldInfo, ScorerResult
from tests.conftest import make_field


def test_default_scorers_returns_list():
    scorers = default_scorers()
    assert isinstance(scorers, list)
    assert len(scorers) >= 2


def test_default_scorers_contains_exact():
    scorers = default_scorers()
    names = [s.name for s in scorers]
    assert "ExactScorer" in names


def test_default_scorers_contains_alias():
    scorers = default_scorers()
    names = [s.name for s in scorers]
    assert "AliasScorer" in names


def test_default_scorers_order():
    """ExactScorer should come before AliasScorer."""
    scorers = default_scorers()
    names = [s.name for s in scorers]
    assert names.index("ExactScorer") < names.index("AliasScorer")


def test_scorer_decorator_registers():
    @scorer("test_fn_scorer", weight=0.5)
    def my_scorer(source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        return ScorerResult(score=0.5, reasoning="test")

    assert "test_fn_scorer" in _REGISTRY


def test_scorer_decorator_name_weight():
    @scorer("test_weight_scorer", weight=0.42)
    def my_scorer2(source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        return None

    wrapped = _REGISTRY["test_weight_scorer"]
    assert wrapped.name == "test_weight_scorer"
    assert wrapped.weight == 0.42


def test_scorer_decorator_callable():
    @scorer("test_callable_scorer", weight=0.9)
    def my_scorer3(source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        return ScorerResult(score=0.9, reasoning="callable test")

    src = make_field("a")
    tgt = make_field("b")
    result = my_scorer3.score(src, tgt)
    assert result is not None
    assert result.score == 0.9


def test_scorer_protocol_satisfied():
    """ExactScorer and AliasScorer satisfy the Scorer protocol."""
    assert isinstance(ExactScorer(), Scorer)
    assert isinstance(AliasScorer(), Scorer)
