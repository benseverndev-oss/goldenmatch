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


# B2 anchor group-winner strategy ---------------------------------------------

from goldenmatch.core.survivorship.winner import group_winner  # noqa: E402


def _rows(spec):
    return [{"__pos__": i, **r} for i, r in enumerate(spec)]


def test_anchor_picks_anchor_bearing_most_complete():
    rows = _rows([
        {"plan_id": None, "plan_name": "Gold", "plan_tier": "G"},
        {"plan_id": "P1", "plan_name": "Gold", "plan_tier": None},
        {"plan_id": "P1", "plan_name": "Gold", "plan_tier": "G"},
    ])
    res = group_winner(rows, ["plan_id", "plan_name", "plan_tier"], strategy="anchor", anchor="plan_id")
    assert res.winner_pos == 2 and res.values["plan_tier"] == "G"


def test_anchor_fallback_to_most_complete_when_none_have_anchor():
    rows = _rows([{"plan_id": None, "plan_name": "A", "plan_tier": "X"},
                  {"plan_id": None, "plan_name": "B", "plan_tier": None}])
    res = group_winner(rows, ["plan_id", "plan_name", "plan_tier"], strategy="anchor", anchor="plan_id")
    assert res.winner_pos == 0


# B3 allow_fill per-cell back-fill --------------------------------------------

def test_allow_fill_fills_winner_nulls_from_strategy_best_other_row():
    # Row 0 wins most_complete (3/4 populated); zip=None filled from row 1.
    rows = _rows([
        {"street": "1 Main St", "city": "LA", "state": "CA", "zip": None},
        {"street": "1 Main", "city": None, "state": None, "zip": "90001"},
    ])
    res = group_winner(rows, ["street", "city", "state", "zip"], strategy="most_complete", allow_fill=True)
    assert res.winner_pos == 0 and res.values["zip"] == "90001"
    assert res.filled == {"zip": 1} and res.confidence == 1.0


def test_allow_fill_off_keeps_winner_null():
    # Row 0 wins most_complete (3/4 populated); without allow_fill, zip stays None.
    rows = _rows([{"street": "1 Main St", "city": "LA", "state": "CA", "zip": None},
                  {"street": "1 Main", "city": None, "state": None, "zip": "90001"}])
    res = group_winner(rows, ["street", "city", "state", "zip"], strategy="most_complete")
    assert res.values["zip"] is None and res.filled == {}


def test_allow_fill_nothing_to_fill():
    rows = _rows([{"a": "x", "b": "y"}, {"a": "p", "b": None}])
    res = group_winner(rows, ["a", "b"], strategy="most_complete", allow_fill=True)
    assert res.filled == {}
