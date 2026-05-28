"""Tests for v0.3 Identity Graph read macros.

Uses the same stub-Jinja harness as test_quality_macros.py /
test_macros.py -- no live dbt-core, no warehouse needed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

jinja2 = pytest.importorskip("jinja2")

_MACROS_DIR = Path(__file__).resolve().parents[1] / "macros"


class _DbtStub:
    @staticmethod
    def string_literal(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"


class _TargetStub:
    def __init__(self, adapter_type: str) -> None:
        self.type = adapter_type


class _AdapterStub:
    def __init__(self, adapter_type, env) -> None:  # noqa: ANN001
        self._adapter_type = adapter_type
        self._env = env

    def dispatch(self, macro_name, namespace):  # noqa: ANN001, ARG002
        for cand in (f"{self._adapter_type}__{macro_name}",
                     f"default__{macro_name}"):
            if cand in self._env.globals:
                return self._env.globals[cand]
        raise RuntimeError(f"no dispatch for {macro_name}")


class _ExceptionsStub:
    @staticmethod
    def raise_compiler_error(msg):  # noqa: ANN001
        raise RuntimeError(msg)


def _build_env(adapter_type: str, macro_filename: str):
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_MACROS_DIR)),
        autoescape=False,
        undefined=jinja2.StrictUndefined,
    )
    env.globals["target"] = _TargetStub(adapter_type)
    env.globals["dbt"] = _DbtStub()
    env.globals["exceptions"] = _ExceptionsStub()
    env.globals["return"] = lambda v: v
    env.globals["adapter"] = _AdapterStub(adapter_type, env)
    template = env.get_template(macro_filename)
    module = template.module
    for name in dir(module):
        if name.startswith("_"):
            continue
        attr = getattr(module, name)
        if callable(attr):
            env.globals[name] = attr
    return env


# Single-arg macros: resolve, view, history (text arg + optional db_path)
@pytest.mark.parametrize("macro_name,sql_pg,sql_duck", [
    ("identity_resolve",
     "goldenmatch.goldenmatch_identity_resolve",
     "goldenmatch_identity_resolve"),
    ("identity_view",
     "goldenmatch.goldenmatch_identity_view",
     "goldenmatch_identity_view"),
    ("identity_history",
     "goldenmatch.goldenmatch_identity_history",
     "goldenmatch_identity_history"),
    ("identity_conflicts",
     "goldenmatch.goldenmatch_identity_conflicts",
     "goldenmatch_identity_conflicts"),
])
def test_single_arg_macro_postgres(macro_name, sql_pg, sql_duck) -> None:
    env = _build_env("postgres", f"{macro_name}.sql")
    fn = env.globals[macro_name]
    sql = fn("ent-abc")
    assert sql_pg in sql
    assert "'ent-abc'" in sql
    # NULL/none db_path renders as empty string literal.
    assert "''" in sql


@pytest.mark.parametrize("macro_name,sql_pg,sql_duck", [
    ("identity_resolve",
     "goldenmatch.goldenmatch_identity_resolve",
     "goldenmatch_identity_resolve"),
    ("identity_view",
     "goldenmatch.goldenmatch_identity_view",
     "goldenmatch_identity_view"),
    ("identity_history",
     "goldenmatch.goldenmatch_identity_history",
     "goldenmatch_identity_history"),
    ("identity_conflicts",
     "goldenmatch.goldenmatch_identity_conflicts",
     "goldenmatch_identity_conflicts"),
])
def test_single_arg_macro_duckdb(macro_name, sql_pg, sql_duck) -> None:
    env = _build_env("duckdb", f"{macro_name}.sql")
    fn = env.globals[macro_name]
    sql = fn("ent-abc")
    assert sql_duck in sql
    # DuckDB path doesn't use the schema prefix.
    assert "goldenmatch." not in sql.replace(sql_duck, "")


# Snowflake dispatch mirrors Postgres -- schema-qualified Snowpark Python
# UDFs in the `goldenmatch` schema. See docs/snowflake-setup.md for
# registration.
@pytest.mark.parametrize("macro_name,sql_sf", [
    ("identity_resolve", "goldenmatch.goldenmatch_identity_resolve"),
    ("identity_view",    "goldenmatch.goldenmatch_identity_view"),
    ("identity_history", "goldenmatch.goldenmatch_identity_history"),
    ("identity_conflicts", "goldenmatch.goldenmatch_identity_conflicts"),
])
def test_single_arg_macro_snowflake(macro_name, sql_sf) -> None:
    env = _build_env("snowflake", f"{macro_name}.sql")
    fn = env.globals[macro_name]
    sql = fn("ent-abc")
    assert sql_sf in sql
    assert "'ent-abc'" in sql
    # NULL/none db_path renders as empty string literal.
    assert "''" in sql


@pytest.mark.parametrize("macro_name", [
    "identity_resolve", "identity_view", "identity_history",
    "identity_conflicts",
])
def test_single_arg_macro_unknown_adapter(macro_name) -> None:
    env = _build_env("bigquery", f"{macro_name}.sql")
    fn = env.globals[macro_name]
    with pytest.raises(
        RuntimeError,
        match=r"(?s)only supported on postgres, duckdb, and\s+snowflake",
    ):
        fn("ent-abc")


@pytest.mark.parametrize("macro_name", [
    "identity_resolve", "identity_view", "identity_history",
])
def test_db_path_explicit_renders_literal(macro_name) -> None:
    env = _build_env("postgres", f"{macro_name}.sql")
    fn = env.globals[macro_name]
    sql = fn("ent-abc", db_path="/var/lib/goldenmatch/identity.db")
    assert "'/var/lib/goldenmatch/identity.db'" in sql


# identity_list -- 3-arg shape (dataset, status, db_path) all optional
def test_identity_list_postgres_no_filters() -> None:
    env = _build_env("postgres", "identity_list.sql")
    fn = env.globals["identity_list"]
    sql = fn()
    assert "goldenmatch.goldenmatch_identity_list" in sql
    # All three args default to empty-string literals.
    assert sql.count("''") == 3


def test_identity_list_postgres_dataset_filter() -> None:
    env = _build_env("postgres", "identity_list.sql")
    fn = env.globals["identity_list"]
    sql = fn(dataset="customers")
    assert "'customers'" in sql
    # status + db_path still empty.
    assert sql.count("''") == 2


def test_identity_list_postgres_all_filters() -> None:
    env = _build_env("postgres", "identity_list.sql")
    fn = env.globals["identity_list"]
    sql = fn(dataset="customers", status="active", db_path="/x/y.db")
    assert "'customers'" in sql
    assert "'active'" in sql
    assert "'/x/y.db'" in sql
    assert sql.count("''") == 0


def test_identity_list_duckdb() -> None:
    env = _build_env("duckdb", "identity_list.sql")
    fn = env.globals["identity_list"]
    sql = fn(dataset="customers", status="active")
    assert "goldenmatch_identity_list" in sql
    # No schema prefix on DuckDB.
    assert "goldenmatch.goldenmatch_identity_list" not in sql


def test_identity_list_snowflake_all_filters() -> None:
    env = _build_env("snowflake", "identity_list.sql")
    fn = env.globals["identity_list"]
    sql = fn(dataset="customers", status="active", db_path="/x/y.db")
    assert "goldenmatch.goldenmatch_identity_list" in sql
    assert "'customers'" in sql
    assert "'active'" in sql
    assert "'/x/y.db'" in sql


def test_identity_list_snowflake_no_filters() -> None:
    env = _build_env("snowflake", "identity_list.sql")
    fn = env.globals["identity_list"]
    sql = fn()
    assert "goldenmatch.goldenmatch_identity_list" in sql
    # All three args default to empty-string literals.
    assert sql.count("''") == 3


def test_identity_list_unknown_adapter_errors() -> None:
    env = _build_env("bigquery", "identity_list.sql")
    fn = env.globals["identity_list"]
    with pytest.raises(
        RuntimeError,
        match=r"(?s)only supported on postgres, duckdb, and\s+snowflake",
    ):
        fn()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
