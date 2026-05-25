"""Tests for the infermap_apply dbt macro (Wave 5 peripheral-breadth sync)."""
from __future__ import annotations

from pathlib import Path

import pytest

jinja2 = pytest.importorskip("jinja2")

_MACROS_DIR = Path(__file__).resolve().parents[1] / "macros"


class _ExceptionsStub:
    @staticmethod
    def raise_compiler_error(msg):  # noqa: ANN001
        raise RuntimeError(msg)


class _AdapterStub:
    @staticmethod
    def quote(name):  # noqa: ANN001
        return '"' + str(name) + '"'


def _render(column_map, relation="my_source"):
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_MACROS_DIR)),
        autoescape=False,
    )
    env.globals["exceptions"] = _ExceptionsStub()
    env.globals["adapter"] = _AdapterStub()
    template = env.get_template("infermap_apply.sql")
    fn = template.module.infermap_apply
    return " ".join(fn(relation, column_map).split())


def test_renders_select_with_aliases():
    sql = _render({"customer_id": "cust_no", "email": "email_addr"})
    assert '"cust_no" AS "customer_id"' in sql
    assert '"email_addr" AS "email"' in sql
    assert "FROM my_source" in sql
    # exactly one comma between the two projected columns
    assert sql.count(" AS ") == 2


def test_single_column_has_no_trailing_comma():
    sql = _render({"id": "raw_id"})
    assert '"raw_id" AS "id"' in sql
    # no dangling comma before FROM
    assert ", FROM" not in sql.replace("  ", " ")


def test_empty_map_raises():
    with pytest.raises(RuntimeError, match="must not be empty"):
        _render({})


def test_non_dict_raises():
    with pytest.raises(RuntimeError, match="must be a dict"):
        _render(["not", "a", "dict"])
