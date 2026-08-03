"""Cross-table entity-type discovery (semantic-model discovery, Phase 2).

`discover_entity_types` groups source tables into the real-world entity types they
realize, via column-semantic signature + value overlap. Design:
`docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`.
"""
from __future__ import annotations

import pyarrow as pa
from goldenmatch.semantic import EntityType, discover_entity_types, discover_keys


def _crm() -> pa.Table:
    return pa.table(
        {
            "customer_id": ["c1", "c2", "c3"],
            "first_name": ["Ann", "Bob", "Cy"],
            "last_name": ["Lee", "Ng", "Poe"],
            "email": ["ann@x.com", "bob@y.com", "cy@z.com"],
        }
    )


def _app_users() -> pa.Table:
    # Different column names for the same Person entity (fname/surname/mail).
    return pa.table(
        {
            "uid": ["u1", "u2", "u3"],
            "fname": ["Dee", "Eli", "Fay"],
            "surname": ["Ray", "Sol", "Tan"],
            "mail": ["dee@a.com", "eli@b.com", "fay@c.com"],
        }
    )


def _orders() -> pa.Table:
    return pa.table(
        {
            "order_id": ["o1", "o2", "o3", "o4"],
            "customer_id": ["c1", "c1", "c2", "c3"],
            "amount": [10.0, 20.0, 30.0, 40.0],
        }
    )


def test_same_shaped_tables_are_one_entity_type() -> None:
    ets = discover_entity_types({"crm": _crm(), "app_users": _app_users()})
    assert ets
    # crm + app_users are both Person surfaces (first_name/last_name/email vs
    # fname/surname/mail canonicalize to the same semantic tokens).
    person = [e for e in ets if set(e.tables) == {"app_users", "crm"}]
    assert person, f"expected crm+app_users grouped, got {[(e.name, e.tables) for e in ets]}"
    e = person[0]
    assert isinstance(e, EntityType)
    assert e.is_multi_table is True
    assert e.name == "person"
    assert "signature" in e.signals


def test_transaction_table_is_its_own_entity_type() -> None:
    ets = discover_entity_types({"crm": _crm(), "orders": _orders()})
    # orders (order_id/customer_id/amount) does not share the person signature.
    by_tables = {frozenset(e.tables): e for e in ets}
    assert frozenset({"orders"}) in by_tables
    assert frozenset({"crm"}) in by_tables


def test_value_overlap_links_tables_by_shared_ids() -> None:
    # Two tables with different schemas but an overlapping email column → same entity.
    a = pa.table({"a_id": ["1", "2"], "email": ["p@x.com", "q@x.com"], "note": ["m", "n"]})
    b = pa.table({"b_id": ["9", "8"], "email": ["p@x.com", "q@x.com"], "flag": ["y", "n"]})
    ets = discover_entity_types({"a": a, "b": b})
    linked = [e for e in ets if set(e.tables) == {"a", "b"}]
    assert linked, f"value overlap on email should link a+b, got {[(e.name, e.tables) for e in ets]}"
    assert "value_overlap" in linked[0].signals


def test_key_by_table_is_recorded_when_keys_supplied() -> None:
    tables = {"crm": _crm(), "app_users": _app_users()}
    keys = {name: discover_keys(t) for name, t in tables.items()}
    ets = discover_entity_types(tables, keys=keys)
    person = [e for e in ets if e.is_multi_table][0]
    # Each table's trustworthy key is recorded as the conformance anchor.
    assert set(person.key_by_table) == {"crm", "app_users"}
    # crm.customer_id classifies as an identifier → preferred anchor.
    assert person.key_by_table["crm"] == "customer_id"
    # app_users has no name/shape-classified id column, so the anchor is some
    # trustworthy unique column (a genuinely ambiguous choice in a toy sample).
    assert person.key_by_table["app_users"] in {"uid", "fname", "surname", "mail"}


def test_multi_table_types_rank_before_singletons() -> None:
    ets = discover_entity_types({"crm": _crm(), "app_users": _app_users(), "orders": _orders()})
    # The person (multi-table) type ranks ahead of the lone orders table.
    assert ets[0].is_multi_table is True
    assert "orders" not in ets[0].tables


def test_confidence_in_range() -> None:
    ets = discover_entity_types({"crm": _crm(), "app_users": _app_users()})
    for e in ets:
        assert 0.0 <= e.confidence <= 1.0
