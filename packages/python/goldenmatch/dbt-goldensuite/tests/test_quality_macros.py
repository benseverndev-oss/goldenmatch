"""Unit tests for the v0.2 GoldenCheck quality-gate macros.

Same stub-Jinja harness as test_macros.py; no live dbt-core or
warehouse needed. We assert the rendered SQL has the right shape per
adapter.
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
    def __init__(self, adapter_type: str, env) -> None:
        self._adapter_type = adapter_type
        self._env = env

    def dispatch(self, macro_name: str, namespace: str):  # noqa: ARG002
        candidates = [
            f"{self._adapter_type}__{macro_name}",
            f"default__{macro_name}",
        ]
        for cand in candidates:
            if cand in self._env.globals:
                return self._env.globals[cand]
        raise RuntimeError(f"no dispatch candidate for {macro_name}")


class _ExceptionsStub:
    @staticmethod
    def raise_compiler_error(msg: str):
        raise RuntimeError(msg)


class _Model:
    """Stand-in for dbt's `model` ref."""

    def __init__(self, identifier: str) -> None:
        self.identifier = identifier

    def __str__(self) -> str:
        # dbt renders `{{ model }}` via the relation's __str__ as the
        # qualified table name. Mirror that for tests using `{{ model }}`
        # directly (e.g. quality_not_empty.sql).
        return self.identifier


def _build_env(adapter_type: str, macro_filename: str) -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_MACROS_DIR)),
        autoescape=False,
        undefined=jinja2.StrictUndefined,
    )
    env.globals["target"] = _TargetStub(adapter_type)
    env.globals["dbt"] = _DbtStub()
    env.globals["exceptions"] = _ExceptionsStub()

    def _return(v):
        return v

    env.globals["return"] = _return
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


# ---------------------------------------------------------------------------
# goldencheck_assert
# ---------------------------------------------------------------------------


def test_assert_postgres_warning_floor() -> None:
    env = _build_env("postgres", "quality_assert.sql")
    fn = env.globals["test_goldencheck_assert"]
    sql = fn(_Model("customers"), min_severity="warning")
    assert "goldencheck.scan_table" in sql
    assert "'customers'" in sql
    assert "'warning'" in sql
    assert "'error'" in sql
    # info NOT in the WHERE list when min_severity=warning.
    assert "'info'" not in sql


def test_assert_postgres_info_floor() -> None:
    env = _build_env("postgres", "quality_assert.sql")
    fn = env.globals["test_goldencheck_assert"]
    sql = fn(_Model("orders"), min_severity="info")
    assert "'info'" in sql
    assert "'warning'" in sql
    assert "'error'" in sql


def test_assert_postgres_with_ignore_list() -> None:
    env = _build_env("postgres", "quality_assert.sql")
    fn = env.globals["test_goldencheck_assert"]
    sql = fn(
        _Model("customers"),
        min_severity="warning",
        ignore_checks=["nullability", "uniqueness"],
    )
    assert "<> ALL" in sql
    assert "'nullability'" in sql
    assert "'uniqueness'" in sql


def test_assert_duckdb_renders() -> None:
    env = _build_env("duckdb", "quality_assert.sql")
    fn = env.globals["test_goldencheck_assert"]
    sql = fn(_Model("customers"), min_severity="error")
    assert "goldencheck_scan_table" in sql
    assert "'customers'" in sql
    assert "'error'" in sql
    # Only error in the IN-clause when min_severity=error.
    assert "'warning'" not in sql
    assert "'info'" not in sql


def test_assert_with_domain_arg() -> None:
    env = _build_env("postgres", "quality_assert.sql")
    fn = env.globals["test_goldencheck_assert"]
    sql = fn(_Model("patients"), domain="healthcare")
    assert "'healthcare'" in sql


def test_assert_snowflake_renders() -> None:
    env = _build_env("snowflake", "quality_assert.sql")
    fn = env.globals["test_goldencheck_assert"]
    sql = fn(_Model("customers"), min_severity="warning")
    # Schema-qualified UDF + Snowflake LATERAL FLATTEN over PARSE_JSON.
    assert "goldencheck.goldencheck_scan_table" in sql
    assert "'customers'" in sql
    assert "PARSE_JSON" in sql
    assert "FLATTEN" in sql
    assert "'warning'" in sql
    assert "'error'" in sql
    assert "'info'" not in sql


def test_assert_snowflake_ignore_list() -> None:
    env = _build_env("snowflake", "quality_assert.sql")
    fn = env.globals["test_goldencheck_assert"]
    sql = fn(
        _Model("customers"),
        min_severity="warning",
        ignore_checks=["nullability", "uniqueness"],
    )
    # Snowflake uses NOT IN (...), not <> ALL(ARRAY[...]).
    assert "NOT IN" in sql
    assert "'nullability'" in sql
    assert "'uniqueness'" in sql


def test_assert_unknown_adapter_errors() -> None:
    env = _build_env("bigquery", "quality_assert.sql")
    fn = env.globals["test_goldencheck_assert"]
    with pytest.raises(
        RuntimeError,
        match="only supported on postgres, duckdb, and snowflake",
    ):
        fn(_Model("anything"))


# ---------------------------------------------------------------------------
# goldencheck_health_gate
# ---------------------------------------------------------------------------


def test_health_gate_postgres_default_threshold() -> None:
    env = _build_env("postgres", "quality_health_gate.sql")
    fn = env.globals["test_goldencheck_health_gate"]
    sql = fn(_Model("customers"))
    assert "goldencheck.health_score" in sql
    assert "80" in sql
    assert "score < 80" in sql


def test_health_gate_postgres_custom_threshold() -> None:
    env = _build_env("postgres", "quality_health_gate.sql")
    fn = env.globals["test_goldencheck_health_gate"]
    sql = fn(_Model("orders"), min_score=95)
    assert "95" in sql
    assert "score < 95" in sql


def test_health_gate_duckdb() -> None:
    env = _build_env("duckdb", "quality_health_gate.sql")
    fn = env.globals["test_goldencheck_health_gate"]
    sql = fn(_Model("customers"), min_score=70)
    assert "goldencheck_health_score" in sql
    assert "70" in sql


def test_health_gate_snowflake() -> None:
    env = _build_env("snowflake", "quality_health_gate.sql")
    fn = env.globals["test_goldencheck_health_gate"]
    sql = fn(_Model("customers"), min_score=85)
    assert "goldencheck.goldencheck_health_score" in sql
    assert "85" in sql
    assert "score < 85" in sql


def test_health_gate_unknown_adapter_errors() -> None:
    env = _build_env("bigquery", "quality_health_gate.sql")
    fn = env.globals["test_goldencheck_health_gate"]
    with pytest.raises(
        RuntimeError,
        match="only supported on postgres, duckdb, and snowflake",
    ):
        fn(_Model("anything"))


## ----------------------------------------------------------------------
## goldencheck_not_empty (folded in from dbt-goldencheck via PR #464)
## ----------------------------------------------------------------------


def test_not_empty_renders_cross_adapter() -> None:
    """The not_empty test is intentionally cross-adapter (no
    extension required); render under both postgres + duckdb stubs
    and confirm the SQL shape is identical."""
    for adapter in ("postgres", "duckdb", "snowflake"):
        env = _build_env(adapter, "quality_not_empty.sql")
        fn = env.globals["test_goldencheck_not_empty"]
        sql = fn(_Model("orders"))
        assert "select 1" in sql.lower()
        assert "from orders" in sql
        assert "having count(*) = 0" in sql


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
