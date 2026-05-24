"""Tests for v0.5 GoldenFlow transform macros."""
from __future__ import annotations

from pathlib import Path

import pytest

jinja2 = pytest.importorskip("jinja2")

_MACROS_DIR = Path(__file__).resolve().parents[1] / "macros"


class _DbtStub:
    @staticmethod
    def string_literal(s):  # noqa: ANN001
        return "'" + str(s).replace("'", "''") + "'"


class _TargetStub:
    def __init__(self, adapter_type) -> None:  # noqa: ANN001
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


def _build_env(adapter_type: str):
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_MACROS_DIR)),
        autoescape=False,
    )
    env.globals["target"] = _TargetStub(adapter_type)
    env.globals["dbt"] = _DbtStub()
    env.globals["exceptions"] = _ExceptionsStub()
    env.globals["return"] = lambda v: v
    env.globals["adapter"] = _AdapterStub(adapter_type, env)
    template = env.get_template("transforms.sql")
    module = template.module
    for name in dir(module):
        if name.startswith("_"):
            continue
        attr = getattr(module, name)
        if callable(attr):
            env.globals[name] = attr
    return env


# DuckDB rendering -- the v0.5 happy path.
@pytest.mark.parametrize("macro,udf", [
    ("normalize_email", "goldenflow_normalize_email"),
    ("normalize_phone", "goldenflow_normalize_phone"),
    ("normalize_date", "goldenflow_normalize_date"),
    ("canonicalize_url", "goldenflow_canonicalize_url"),
    ("canonicalize_address", "goldenflow_canonicalize_address"),
    ("strip_whitespace", "goldenflow_strip"),
    ("whitespace_normalize", "goldenflow_whitespace_normalize"),
])
def test_duckdb_renders_udf_call(macro, udf) -> None:
    env = _build_env("duckdb")
    fn = env.globals[macro]
    sql = fn("email_raw")
    assert udf in sql
    assert "email_raw" in sql


def test_normalize_name_proper_duckdb() -> None:
    env = _build_env("duckdb")
    fn = env.globals["normalize_name"]
    sql = fn("name_raw", mode="proper")
    assert "goldenflow_normalize_name_proper" in sql


def test_normalize_name_upper_uses_sql_builtin() -> None:
    """upper mode skips the UDF + uses standard SQL UPPER()."""
    env = _build_env("duckdb")
    fn = env.globals["normalize_name"]
    sql = fn("name_raw", mode="upper")
    assert "UPPER(name_raw)" in sql
    assert "goldenflow_normalize_name" not in sql


def test_normalize_name_lower_uses_sql_builtin() -> None:
    env = _build_env("duckdb")
    fn = env.globals["normalize_name"]
    sql = fn("name_raw", mode="lower")
    assert "LOWER(name_raw)" in sql


def test_normalize_name_invalid_mode_errors() -> None:
    env = _build_env("duckdb")
    fn = env.globals["normalize_name"]
    with pytest.raises(RuntimeError, match="mode must be one of"):
        fn("name_raw", mode="screaming-snake")


# Postgres -- compile-error in v0.5 (pgrx wrappers deferred).
@pytest.mark.parametrize("macro", [
    "normalize_email",
    "normalize_phone",
    "normalize_date",
    "canonicalize_url",
    "canonicalize_address",
    "whitespace_normalize",
])
def test_postgres_errors_with_pgrx_followup_hint(macro) -> None:
    env = _build_env("postgres")
    fn = env.globals[macro]
    with pytest.raises(RuntimeError, match="(?s).*pgrx.*wrappers"):
        fn("col")


def test_strip_whitespace_default_falls_back_to_sql_trim() -> None:
    """strip_whitespace is the only transform with a usable default__
    branch (standard SQL TRIM works everywhere)."""
    env = _build_env("snowflake")
    fn = env.globals["strip_whitespace"]
    sql = fn("col")
    assert "TRIM(col)" in sql


# Other adapters -- compile error pointing at the Python helper.
@pytest.mark.parametrize("macro", [
    "normalize_email",
    "normalize_phone",
    "normalize_date",
    "canonicalize_url",
    "canonicalize_address",
    "whitespace_normalize",
])
def test_unknown_adapter_errors(macro) -> None:
    env = _build_env("snowflake")
    fn = env.globals[macro]
    with pytest.raises(RuntimeError, match="(?s).*out-of-band"):
        fn("col")


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
