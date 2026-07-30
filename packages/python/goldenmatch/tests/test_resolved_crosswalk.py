"""Tests for goldenmatch.semantic.build_resolved_crosswalk (wedge B: resolve once)."""
from __future__ import annotations

import pyarrow as pa
import pytest
from goldenmatch.semantic import ResolvedCrosswalk, build_resolved_crosswalk

# customer_id 100 and 101 are byte-identical people -> ER collapses them to one
# durable entity; the rest are distinct.
_NAMES = ["Robert Smith", "Robert Smith", "Jane Doe", "Alice Ray", "Tom Lee",
          "Nina Fox", "Ed Poe", "Uma Roy", "Cy Vane", "Al Ives", "Bo Katz", "Di Nash"]
_CITIES = ["Boston", "Boston", "Denver", "Miami", "Reno", "Akron",
           "Provo", "Ames", "Waco", "Bend", "Ojai", "Enid"]
_CIDS = [100, 101, 200, 201, 202, 203, 204, 205, 206, 207, 208, 209]


def _frame():
    return pa.table({"customer_id": _CIDS, "name": _NAMES, "city": _CITIES})


def test_crosswalk_collapses_duplicates_to_one_entity():
    xw = build_resolved_crosswalk(_frame(), source_pk="customer_id", source_name="crm")
    assert isinstance(xw, ResolvedCrosswalk)
    assert xw.n_records == 12
    assert xw.n_entities == 11          # 100 & 101 share one entity
    assert xw.unmapped == 0
    assert xw.reduction_ratio == pytest.approx(1 - 11 / 12)

    d = xw.table.to_pydict()
    by_pk = {pk: eid for pk, eid in zip(d["source_pk"], d["resolved_entity_id"])}
    # the dirty duplicate keys map to the SAME durable entity id
    assert by_pk["100"] == by_pk["101"]
    # distinct people keep distinct ids
    assert by_pk["200"] != by_pk["100"]
    # every id is a durable (UUID-shaped) control-plane id, not a row index
    assert all(eid and "-" in eid for eid in d["resolved_entity_id"])


def test_crosswalk_columns_and_order():
    xw = build_resolved_crosswalk(_frame(), source_pk="customer_id", source_name="crm")
    assert xw.table.column_names == ["source", "source_pk", "resolved_entity_id"]
    d = xw.table.to_pydict()
    assert d["source_pk"] == [str(c) for c in _CIDS]   # input order preserved
    assert set(d["source"]) == {"crm"}


def test_durable_ids_stable_across_runs(tmp_path):
    """The point of 'resolve once': re-running against the same store yields the
    same entity ids for the same source keys."""
    store = str(tmp_path / "identity.db")
    a = build_resolved_crosswalk(_frame(), source_pk="customer_id",
                                 source_name="crm", store_path=store)
    b = build_resolved_crosswalk(_frame(), source_pk="customer_id",
                                 source_name="crm", store_path=store)
    ma = dict(zip(a.table.column("source_pk").to_pylist(),
                  a.table.column("resolved_entity_id").to_pylist()))
    mb = dict(zip(b.table.column("source_pk").to_pylist(),
                  b.table.column("resolved_entity_id").to_pylist()))
    assert ma == mb                     # stable across runs
    assert a.store_path == store        # durable store echoed back


def test_ephemeral_store_notes_non_durability():
    xw = build_resolved_crosswalk(_frame(), source_pk="customer_id", source_name="crm")
    assert xw.store_path is None
    assert "ephemeral" in xw.note


def test_missing_source_pk_raises():
    with pytest.raises(ValueError):
        build_resolved_crosswalk(_frame(), source_pk="nope")
