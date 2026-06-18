import pytest
from goldenmatch.config.schemas import GoldenGroupRule
from pydantic import ValidationError


def test_defaults():
    g = GoldenGroupRule(name="g", columns=["a", "b"])
    assert g.anchor is None and g.allow_fill is False


def test_allow_fill_orthogonal():
    g = GoldenGroupRule(name="g", columns=["a", "b"], allow_fill=True)
    assert g.allow_fill is True


def test_anchor_strategy_valid():
    g = GoldenGroupRule(name="g", columns=["plan_id", "plan_name"], strategy="anchor", anchor="plan_id")
    assert g.strategy == "anchor" and g.anchor == "plan_id"


def test_anchor_requires_anchor_column():
    with pytest.raises(ValidationError):
        GoldenGroupRule(name="g", columns=["a", "b"], strategy="anchor")


def test_anchor_must_be_in_columns():
    with pytest.raises(ValidationError):
        GoldenGroupRule(name="g", columns=["a", "b"], strategy="anchor", anchor="c")


def test_anchor_with_non_anchor_strategy_rejected():
    with pytest.raises(ValidationError):
        GoldenGroupRule(name="g", columns=["a", "b"], strategy="most_complete", anchor="a")
