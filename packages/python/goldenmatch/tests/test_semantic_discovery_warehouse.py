"""Warehouse-scale derivation off information_schema (PR-17).

A warehouse has hundreds of tables; you can't pull them all into memory just to learn
where to start. `information_schema` gives you a cheap CANDIDATE structure — columns,
declared PK/FK — but declared constraints are NOT enforced by Snowflake/BigQuery/Redshift,
so they're a hypothesis to certify, never a certificate. This slice reads that metadata
into a planning manifest and ranks which tables to pull + certify first; the actual grain
is still PROVEN from data by the normal pipeline.
"""
from __future__ import annotations

import pyarrow as pa
from goldenmatch.semantic import (
    WarehouseManifest,
    discover_from_manifest,
    plan_certification,
    read_information_schema,
)

# --- fixtures: rows shaped like information_schema relations ----------------------


def _columns() -> list[dict]:
    return [
        {"table_name": "customers", "column_name": "customer_id", "data_type": "varchar",
         "ordinal_position": 1, "is_nullable": "NO"},
        {"table_name": "customers", "column_name": "region", "data_type": "varchar",
         "ordinal_position": 2, "is_nullable": "YES"},
        {"table_name": "orders", "column_name": "order_id", "data_type": "varchar",
         "ordinal_position": 1, "is_nullable": "NO"},
        {"table_name": "orders", "column_name": "customer_id", "data_type": "varchar",
         "ordinal_position": 2, "is_nullable": "YES"},
        {"table_name": "orders", "column_name": "amount", "data_type": "double",
         "ordinal_position": 3, "is_nullable": "YES"},
    ]


def _table_constraints() -> list[dict]:
    return [
        {"table_name": "customers", "constraint_name": "customers_pk",
         "constraint_type": "PRIMARY KEY"},
        {"table_name": "orders", "constraint_name": "orders_pk",
         "constraint_type": "PRIMARY KEY"},
        {"table_name": "orders", "constraint_name": "orders_customer_fk",
         "constraint_type": "FOREIGN KEY"},
    ]


def _key_column_usage() -> list[dict]:
    return [
        {"table_name": "customers", "constraint_name": "customers_pk",
         "column_name": "customer_id", "ordinal_position": 1},
        {"table_name": "orders", "constraint_name": "orders_pk",
         "column_name": "order_id", "ordinal_position": 1},
        {"table_name": "orders", "constraint_name": "orders_customer_fk",
         "column_name": "customer_id", "ordinal_position": 1,
         "referenced_table_name": "customers", "referenced_column_name": "customer_id"},
    ]


# --- reading ---------------------------------------------------------------------


def test_reads_columns_and_declared_pk_uncertified():
    m = read_information_schema(_columns(), _table_constraints(), _key_column_usage())
    assert isinstance(m, WarehouseManifest)
    cust = next(t for t in m.tables if t.name == "customers")
    assert [c.name for c in cust.columns] == ["customer_id", "region"]
    assert cust.columns[0].data_type == "varchar"
    assert cust.columns[1].nullable is True
    assert cust.declared_pk == ("customer_id",)
    assert cust.has_declared_pk is True


def test_reads_declared_fk_with_referenced_table():
    m = read_information_schema(_columns(), _table_constraints(), _key_column_usage())
    orders = next(t for t in m.tables if t.name == "orders")
    assert len(orders.declared_fks) == 1
    fk = orders.declared_fks[0]
    assert fk.columns == ("customer_id",)
    assert fk.to_table == "customers"
    assert fk.to_columns == ("customer_id",)
    # information_schema declarations are hypotheses, never certificates.
    assert fk.certified is False


def test_accepts_pyarrow_tables_as_input():
    cols = pa.Table.from_pylist(_columns())
    tc = pa.Table.from_pylist(_table_constraints())
    kcu = pa.Table.from_pylist(_key_column_usage())
    m = read_information_schema(cols, tc, kcu)
    assert {t.name for t in m.tables} == {"customers", "orders"}


def test_row_count_from_tables_relation():
    tables = [{"table_name": "customers", "row_count": 3},
              {"table_name": "orders", "row_count": 9}]
    m = read_information_schema(_columns(), _table_constraints(), _key_column_usage(),
                               tables=tables)
    assert next(t for t in m.tables if t.name == "customers").row_count == 3


# --- planning --------------------------------------------------------------------


def test_plan_ranks_fk_referenced_spine_first_and_warns_unproven():
    m = read_information_schema(_columns(), _table_constraints(), _key_column_usage())
    plan = plan_certification(m)
    # customers is referenced by orders' FK (in-degree 1) -> it's the spine, certify first.
    assert plan[0].table == "customers"
    assert {s.table for s in plan} == {"customers", "orders"}
    # every table with a declared PK must carry an "unproven / not enforced" warning.
    for step in plan:
        assert any("not enforced" in w for w in step.warnings)


# --- the thesis: declarations are ranked on, never trusted ------------------------


def test_discover_from_manifest_certifies_from_data_not_declaration():
    # the warehouse DECLARES customer_id a PK, but the actual data violates it
    # (duplicated customer_id) -> the certified pipeline must NOT trust the declaration.
    m = read_information_schema(_columns(), _table_constraints(), _key_column_usage())
    data = {
        "customers": pa.table({"customer_id": ["c1", "c1", "c2"],  # dup -> not unique
                               "region": ["w", "e", "w"]}),
        "orders": pa.table({"order_id": ["o1", "o2", "o3"],
                            "customer_id": ["c1", "c1", "c2"],
                            "amount": [1.0, 2.0, 3.0]}),
    }
    model = discover_from_manifest(m, loader=lambda name: data[name])
    cust = next(t for t in model.tables if t.table == "customers")
    assert cust.grain_trustworthy is False  # proven from data, not the declared PK


def test_manifest_to_dict_marks_declarations_uncertified():
    m = read_information_schema(_columns(), _table_constraints(), _key_column_usage())
    d = m.to_dict()
    orders = next(t for t in d["tables"] if t["name"] == "orders")
    assert orders["declared_fks"][0]["certified"] is False
    assert orders["declared_pk"] == ["order_id"]
