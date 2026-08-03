"""Dimension hierarchies via FD (PR-11).

Among a table's dimension columns, detect near-functional-dependencies (a finer level
determines a coarser one) and extract coarse->fine drill hierarchies. Deterministic,
default-on, attached to ProposedModel.hierarchies + emitted into
meta.goldenmatch.hierarchies. Driven on a stores geo fixture.
"""
from __future__ import annotations

import pyarrow as pa
import yaml
from goldenmatch.semantic import discover_semantic_model


def _stores() -> pa.Table:
    """store_id is the grain; city -> state -> country is a clean geo hierarchy
    (each city has one state, each state one country)."""
    return pa.table({
        "store_id": ["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8"],
        "city": ["NYC", "NYC", "Boston", "LA", "LA", "SF", "Toronto", "Toronto"],
        "state": ["NY", "NY", "MA", "CA", "CA", "CA", "ON", "ON"],
        "country": ["US", "US", "US", "US", "US", "US", "CA", "CA"],
    })


# --- FD hierarchy detection -----------------------------------------------------


def test_discover_hierarchies_finds_geo_chain():
    from goldenmatch.semantic.discovery.hierarchies import discover_hierarchies

    hs = discover_hierarchies(_stores(), ["city", "state", "country"])
    assert len(hs) == 1
    # Ordered coarse -> fine (the drill-down path).
    assert hs[0].levels == ["country", "state", "city"]
    assert hs[0].confidence >= 0.95


def test_no_hierarchy_among_independent_columns():
    from goldenmatch.semantic.discovery.hierarchies import discover_hierarchies

    # color and size are independent — neither determines the other.
    t = pa.table({
        "color": ["red", "blue", "red", "blue", "green", "green"],
        "size": ["S", "S", "M", "M", "L", "S"],
    })
    assert discover_hierarchies(t, ["color", "size"]) == []


def test_near_fd_tolerates_a_dirty_row():
    from goldenmatch.semantic.discovery.hierarchies import discover_hierarchies

    # One mis-keyed row (LA -> NY) must NOT kill the city->state hierarchy (near-FD).
    t = pa.table({
        "city": ["NYC", "NYC", "LA", "LA", "LA", "LA", "LA", "LA", "LA", "NY_BUG_LA"],
        "state": ["NY", "NY", "CA", "CA", "CA", "CA", "CA", "CA", "CA", "NY"],
    })
    hs = discover_hierarchies(t, ["city", "state"])
    assert any(h.levels == ["state", "city"] for h in hs)


# --- integration: default-on, on the model + emitted meta -----------------------


def test_hierarchies_flow_through_discover_semantic_model_and_to_dict():
    m = discover_semantic_model({"stores": _stores()})
    assert any(set(h.levels) == {"city", "state", "country"} for h in m.hierarchies)
    d = m.to_dict()
    assert any(h["levels"] == ["country", "state", "city"] for h in d["hierarchies"])


def test_hierarchies_emitted_into_cube_meta():
    m = discover_semantic_model({"stores": _stores()}, dialect="cube")
    cube = {c["name"]: c for c in yaml.safe_load(m.yaml)["cubes"]}["stores"]
    hier = cube["meta"]["goldenmatch"]["hierarchies"]
    assert any(h["levels"] == ["country", "state", "city"] for h in hier)
