"""Tests for the wedge-B crosswalk materialization (run_goldenmatch_crosswalk).

Resolves identity once and materializes the durable
``{source, source_pk, resolved_entity_id}`` crosswalk table in the warehouse —
the conformed join key a semantic layer groups metrics by.
"""
from __future__ import annotations

import duckdb
import pytest

try:
    import dbt_goldensuite  # noqa: F401
except ImportError:
    pytest.skip("dbt-goldensuite not installed", allow_module_level=True)

try:
    # The crosswalk helper needs the semantic-layer wedge (ADR 0050). The dbt CI
    # lane installs goldenmatch from PyPI, whose released build may predate the
    # `goldenmatch.semantic` module — skip there until it ships (the feature is
    # covered wherever a semantic-capable goldenmatch is present, e.g. workspace dev).
    import goldenmatch.semantic  # noqa: F401
except ImportError:
    pytest.skip("goldenmatch.semantic not available", allow_module_level=True)

from dbt_goldensuite.materialize import run_goldenmatch_crosswalk


def _seed(con: duckdb.DuckDBPyConnection) -> None:
    # customer_id 1/2 are byte-identical people -> ER collapses them to one
    # durable entity; the rest are distinct.
    con.execute(
        "CREATE TABLE raw AS SELECT * FROM (VALUES "
        "(1,'Robert Smith','Boston'),"
        "(2,'Robert Smith','Boston'),"
        "(3,'Jane Doe','Denver'),"
        "(4,'Alice Ray','Miami'),"
        "(5,'Tom Lee','Reno'),"
        "(6,'Nina Fox','Akron')"
        ") t(customer_id, name, city)"
    )


def test_crosswalk_materializes_durable_entity_ids(tmp_path):
    db = str(tmp_path / "wh.duckdb")
    store = str(tmp_path / "identity.db")
    con = duckdb.connect(db)
    _seed(con)
    con.close()

    res = run_goldenmatch_crosswalk(
        input_table="raw", source_pk="customer_id",
        output_table="customer_crosswalk", database=db,
        source_name="crm", store_path=store,
    )
    assert res["input_rows"] == 6
    assert res["n_records"] == 6
    assert res["unmapped"] == 0
    assert res["durable"] is True
    assert res["store_path"] == store

    con = duckdb.connect(db)
    rows = con.execute(
        "SELECT source, source_pk, resolved_entity_id FROM customer_crosswalk ORDER BY source_pk"
    ).fetchall()
    con.close()

    assert len(rows) == 6
    # the crosswalk carries exactly the wedge-B triple
    assert all(r[0] == "crm" for r in rows)
    by_pk = {r[1]: r[2] for r in rows}
    # every resolved id is a durable (UUID-shaped) control-plane id, not a row index
    assert all(eid and "-" in eid for eid in by_pk.values())
    # the byte-identical duplicate keys map to the SAME durable entity
    assert by_pk["1"] == by_pk["2"]
    # distinct people keep distinct ids
    assert by_pk["3"] != by_pk["1"]


def test_crosswalk_requires_output_table(tmp_path):
    with pytest.raises(TypeError):
        run_goldenmatch_crosswalk(input_table="raw", source_pk="customer_id")


def test_crosswalk_ephemeral_when_no_store(tmp_path):
    db = str(tmp_path / "wh.duckdb")
    con = duckdb.connect(db)
    _seed(con)
    con.close()
    res = run_goldenmatch_crosswalk(
        input_table="raw", source_pk="customer_id",
        output_table="xw", database=db, source_name="crm",
    )
    assert res["durable"] is False
    assert res["store_path"] is None      # ephemeral store not echoed
    assert res["n_records"] == 6
