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
    # select_autoescape() is CodeQL's recommended pattern (not flagged, unlike a
    # bare autoescape=False); it returns False for our `.sql` template, so the
    # rendered SQL is not HTML-escaped (autoescape=True would turn `>` into `&gt;`).
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_MACROS_PATH.parent)),
        autoescape=jinja2.select_autoescape(),
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


def test_grain_advisory_by_default():
    # grain is recorded but NOT folded into the group -> key-only GROUP BY.
    h = _load_macros()
    sql = h.goldenmatch_key_integrity_sql(model="m", key="customer_id", grain=["date"])
    assert "GROUP BY customer_id" in sql
    assert "GROUP BY customer_id, date" not in sql


def test_grain_strict_folds_grain_into_group():
    h = _load_macros()
    sql = h.goldenmatch_key_integrity_sql(
        model="m", key="customer_id", grain=["date"], grain_strict=True
    )
    assert "GROUP BY customer_id, date" in sql


def test_grain_strict_dedupes_grain_already_in_key():
    h = _load_macros()
    sql = h.goldenmatch_key_integrity_sql(
        model="m", key="customer_id", grain=["customer_id"], grain_strict=True
    )
    assert "GROUP BY customer_id\n" in sql or "GROUP BY customer_id " in sql
    assert "customer_id, customer_id" not in sql


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


def _make_daily_con():
    con = duckdb.connect()
    # customer 1 repeats across two DATES (legit at daily grain); 2 is single.
    con.execute(
        "CREATE TABLE daily AS SELECT * FROM (VALUES "
        "(1,'2026-01-01',10),(1,'2026-01-02',20),(2,'2026-01-01',5)"
        ") v(customer_id, date, revenue)"
    )
    return con


def test_grain_strict_certifies_daily_fact():
    h = _load_macros()
    con = _make_daily_con()
    # key-only: customer 1 fans out 2x -> FAILS.
    key_only = h.goldenmatch_key_integrity_sql(
        model="daily", key="customer_id", measures=["revenue"], grain=["date"]
    )
    assert len(con.sql(key_only).fetchall()) == 1
    # grain_strict: (customer_id, date) is unique -> PASSES.
    strict = h.goldenmatch_key_integrity_sql(
        model="daily", key="customer_id", measures=["revenue"],
        grain=["date"], grain_strict=True,
    )
    assert len(con.sql(strict).fetchall()) == 0


def test_grain_strict_still_flags_true_duplicate_at_grain():
    h = _load_macros()
    con = duckdb.connect()
    # (customer_id, date) genuinely repeated -> grain can't excuse it.
    con.execute(
        "CREATE TABLE dup AS SELECT * FROM (VALUES "
        "(1,'2026-01-01',10),(1,'2026-01-01',10),(2,'2026-01-01',5)"
        ") v(customer_id, date, revenue)"
    )
    strict = h.goldenmatch_key_integrity_sql(
        model="dup", key="customer_id", measures=["revenue"],
        grain=["date"], grain_strict=True,
    )
    assert len(con.sql(strict).fetchall()) == 1


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
