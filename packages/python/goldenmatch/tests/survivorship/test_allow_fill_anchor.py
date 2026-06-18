import json

import polars as pl
import pytest
from goldenmatch.config.schemas import GoldenGroupRule, GoldenRulesConfig
from goldenmatch.core.golden import ClusterProvenance, GroupProvenance
from goldenmatch.core.lineage import render_group_provenance_line, save_lineage
from goldenmatch.core.survivorship.conditions import build_resolution_order
from goldenmatch.core.survivorship.resolve import resolve_cluster
from goldenmatch.core.survivorship.winner import group_winner
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


# C1 resolve.py threading + filled remap --------------------------------------


def test_resolve_allow_fill_records_filled_row_id():
    df = pl.DataFrame({
        "__cluster_id__": [5, 5], "__row_id__": [10, 11],
        "street": ["1 Main St", "1 Main"], "city": ["LA", None], "zip": [None, "90001"],
    })
    rules = GoldenRulesConfig(default_strategy="most_complete", field_groups=[
        GoldenGroupRule(name="addr", columns=["street", "city", "zip"], allow_fill=True)])
    order = build_resolution_order(rules.field_rules, rules.field_groups, ["street", "city", "zip"])
    rec, prov = resolve_cluster(df, rules, order, provenance=True, cluster_id=5)
    gp = prov.groups[0]
    # row 0 is most-complete (street+city, 2/3); zip filled from row 1 (row_id 11)
    assert gp.values["zip"] == "90001"
    assert gp.filled == {"zip": 11}
    assert rec["zip"]["value"] == "90001"
    assert rec["zip"]["source_row_id"] == 11


# D2 back-fill NL line --------------------------------------------------------


def test_render_group_line_includes_backfill():
    gp = GroupProvenance(name="mailing_address", columns=["street", "city", "zip"], strategy="most_complete",
                         winner_row_id=7, winner_source=None, values={}, tie=False, confidence=1.0, filled={"zip": 12})
    out = render_group_provenance_line(gp)
    assert "promoted together from record 7" in out
    assert "mailing_address: zip back-filled from record 12" in out


def test_render_group_line_no_fill_unchanged():
    gp = GroupProvenance(name="addr", columns=["a", "b"], strategy="most_complete",
                         winner_row_id=7, winner_source=None, values={}, tie=False, confidence=1.0)
    out = render_group_provenance_line(gp)
    assert "back-filled" not in out


# D3 omit empty filled in serialization ---------------------------------------


def _cp_filled(filled):
    g = GroupProvenance(name="addr", columns=["street", "zip"], strategy="most_complete",
                        winner_row_id=7, winner_source=None, values={}, tie=False, confidence=1.0, filled=filled)
    return [ClusterProvenance(cluster_id=5, cluster_quality="strong", cluster_confidence=0.9, fields={}, groups=[g])]


def test_filled_omitted_when_empty(tmp_path):
    path = save_lineage([], tmp_path, "run", golden_provenance=_cp_filled({}))
    grp = json.loads(path.read_text(encoding="utf-8"))["golden_records"][0]["groups"][0]
    assert "filled" not in grp


def test_filled_present_when_nonempty(tmp_path):
    path = save_lineage([], tmp_path, "run", golden_provenance=_cp_filled({"zip": 12}))
    grp = json.loads(path.read_text(encoding="utf-8"))["golden_records"][0]["groups"][0]
    assert grp["filled"] == {"zip": 12}


# E1 combined anchor + allow_fill + parity ------------------------------------


def test_anchor_plus_allow_fill():
    rows = _rows([
        {"plan_id": "P1", "plan_name": "Gold", "plan_tier": None},   # anchor present, 2/3 -> winner
        {"plan_id": None, "plan_name": "Gold", "plan_tier": "G"},     # has tier
    ])
    res = group_winner(rows, ["plan_id", "plan_name", "plan_tier"],
                       strategy="anchor", anchor="plan_id", allow_fill=True)
    assert res.winner_pos == 0
    assert res.values["plan_tier"] == "G" and res.filled == {"plan_tier": 1}


def test_no_levers_byte_identical():
    # strict lock-step (no allow_fill, default strategy) pins the winner's null + empty filled
    rows = _rows([{"a": "x", "b": None}, {"a": "p", "b": "q"}])
    res = group_winner(rows, ["a", "b"], strategy="most_complete")
    assert res.values["b"] is None and res.filled == {}
