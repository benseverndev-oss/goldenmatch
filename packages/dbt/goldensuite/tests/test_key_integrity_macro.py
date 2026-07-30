"""Tests for the goldenmatch_key_integrity generic test.

Mirrors ``test_match_quality.py``: the ``{% test %}`` wrapper delegates to the
pure-return ``goldenmatch_key_integrity_sql`` helper (using the ``dbt_goldensuite.``
namespace plain Jinja2 lacks), so we render-test the helper and execute the
emitted SQL against DuckDB — the load-bearing correctness check.
"""
from __future__ import annotations

from pathlib import Path

import pytest

jinja2 = pytest.importorskip("jinja2")
import jinja2.ext  # noqa: E402, F811

none = None

_MACROS_PATH = Path(__file__).resolve().parents[1] / "macros" / "test_key_integrity.sql"


class _TestTagExtension(jinja2.ext.Extension):
    """No-op parser for dbt's custom ``{% test %}`` tag (plain Jinja2 lacks it)."""

    tags = {"test"}

    def parse(self, parser):  # noqa: ANN001
        while parser.stream.current.type != "block_end":
            next(parser.stream)
        parser.parse_statements(["name:endtest"], drop_needle=True)
        return []


class _ExceptionsStub:
    @staticmethod
    def raise_compiler_error(msg):  # noqa: ANN001
        raise RuntimeError(msg)


def _load_macros():
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_MACROS_PATH.parent)),
        autoescape=False,
        extensions=["jinja2.ext.do", _TestTagExtension],
    )
    env.globals["exceptions"] = _ExceptionsStub()
    env.globals["return"] = lambda v: v
    return env.get_template(_MACROS_PATH.name).module


# --- render tests --------------------------------------------------------------


def test_sql_shape_single_key_no_measures():
    h = _load_macros()
    sql = h.goldenmatch_key_integrity_sql(model="m", key="customer_id")
    assert "GROUP BY customer_id" in sql
    assert "uniqueness" in sql and "max_fan_out" in sql
    assert "__fan_out" not in sql  # no measures -> no fan-out clause


def test_sql_shape_composite_key_and_measures():
    h = _load_macros()
    sql = h.goldenmatch_key_integrity_sql(model="m", key=["a", "b"], measures=["revenue"])
    assert "GROUP BY a, b" in sql
    assert "MAX(revenue) AS revenue__rep" in sql
    assert "revenue__fan_out" in sql


def test_empty_key_errors():
    h = _load_macros()
    with pytest.raises(RuntimeError):
        h.goldenmatch_key_integrity_sql(model="m", key=[])


# --- DuckDB execution ----------------------------------------------------------

duckdb = pytest.importorskip("duckdb")


def _make_con():
    con = duckdb.connect()
    # customer 1 appears twice with identical revenue (fan-out); 2 is clean.
    con.execute("CREATE TABLE t AS SELECT * FROM (VALUES (1,10),(1,10),(2,5)) v(customer_id, revenue)")
    con.execute("CREATE TABLE clean AS SELECT * FROM (VALUES (1,10),(2,5),(3,7)) v(customer_id, revenue)")
    return con


def test_duplicated_key_fails():
    h = _load_macros()
    con = _make_con()
    sql = h.goldenmatch_key_integrity_sql(model="t", key="customer_id",
        measures=["revenue"], max_fan_out=1.0, min_uniqueness=1.0)
    rows = con.sql(sql).fetchall()
    assert len(rows) == 1                       # uniqueness 0.5, fan-out 2 -> fail
    uniqueness, max_fan_out, rev_fan_out = rows[0]
    assert abs(uniqueness - 0.5) < 1e-9
    assert max_fan_out == 2
    assert abs(rev_fan_out - (25.0 / 15.0)) < 1e-9


def test_clean_key_passes():
    h = _load_macros()
    con = _make_con()
    sql = h.goldenmatch_key_integrity_sql(model="clean", key="customer_id",
        measures=["revenue"], max_fan_out=1.0, min_uniqueness=1.0)
    assert len(con.sql(sql).fetchall()) == 0    # unique, no fan-out -> pass


def test_tolerant_thresholds_pass_dup():
    h = _load_macros()
    con = _make_con()
    # allow up to 2x fan-out and 0.5 uniqueness -> the dup no longer violates
    sql = h.goldenmatch_key_integrity_sql(model="t", key="customer_id",
        measures=["revenue"], max_fan_out=2.0, min_uniqueness=0.5)
    assert len(con.sql(sql).fetchall()) == 0


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
