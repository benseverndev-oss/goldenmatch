"""Unit tests for the dbt-goldenmatch macros (closes #210, Phase 6C).

Renders each macro against a stub Jinja environment that mimics the
dbt context. Avoids a live `dbt-core` import path -- a real dbt
integration test would need a running Postgres + DuckDB, which is
outside the per-package CI lane's scope. The CI integration test
lives at the SQL-extension level (PG matrix in `rust_pgrx` lane,
DuckDB in `duckdb_extensions` lane); these unit tests just assert
the rendered SQL is correctly shaped for each adapter.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# Jinja2 is a transitive dep of dbt-core but not directly required by
# the goldenmatch root venv. Skip cleanly when running outside the
# dbt-goldenmatch lane.
jinja2 = pytest.importorskip("jinja2")


_MACROS_DIR = Path(__file__).resolve().parents[1] / "macros"


# ---------------------------------------------------------------------------
# Jinja environment + dbt stub context
# ---------------------------------------------------------------------------


class _DbtStub:
    """Minimal stub for the `dbt` global. Just `string_literal`."""

    @staticmethod
    def string_literal(s: str) -> str:
        # Single-quote with backslash escape -- mirrors dbt-core's default
        # macro for postgres / duckdb. Good enough for shape-checks.
        escaped = s.replace("'", "''")
        return f"'{escaped}'"


class _TargetStub:
    def __init__(self, adapter_type: str) -> None:
        self.type = adapter_type


class _AdapterStub:
    def __init__(self, adapter_type: str, env: jinja2.Environment) -> None:
        self._adapter_type = adapter_type
        self._env = env

    def dispatch(self, macro_name: str, namespace: str):  # noqa: ARG002
        # Look up the adapter-specific macro in the same template
        # (e.g. `postgres__goldenmatch_file_field_correction`) and
        # fall back to `default__<macro>`.
        candidates = [
            f"{self._adapter_type}__{macro_name}",
            f"default__{macro_name}",
        ]
        for cand in candidates:
            if cand in self._env.globals:
                return self._env.globals[cand]
        raise RuntimeError(f"no dispatch candidate for {macro_name} on {self._adapter_type}")


def _exceptions_stub():
    class _Ex:
        @staticmethod
        def raise_compiler_error(msg: str):
            raise RuntimeError(msg)
    return _Ex()


def _build_env(adapter_type: str, macro_filename: str) -> jinja2.Environment:
    """Load a macro template + return a Jinja env with dbt-shaped globals."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_MACROS_DIR)),
        autoescape=False,
        undefined=jinja2.StrictUndefined,
    )
    # Wire `target`, `adapter`, `dbt`, `exceptions`, `tojson`, `return`.
    env.globals["target"] = _TargetStub(adapter_type)
    env.globals["dbt"] = _DbtStub()
    env.globals["exceptions"] = _exceptions_stub()
    env.globals["tojson"] = lambda v: json.dumps(v)
    captured: dict[str, object] = {}

    def _return(v):
        captured["value"] = v
        return v

    env.globals["return"] = _return
    env.globals["adapter"] = _AdapterStub(adapter_type, env)
    # Load + register the macros from the template as callable globals.
    template = env.get_template(macro_filename)
    module = template.module
    for name in dir(module):
        if name.startswith("_"):
            continue
        attr = getattr(module, name)
        if callable(attr):
            env.globals[name] = attr
    env.globals["_captured"] = captured
    return env


# ---------------------------------------------------------------------------
# file_field_correction
# ---------------------------------------------------------------------------


def test_field_correction_postgres_render() -> None:
    env = _build_env("postgres", "file_field_correction.sql")
    fn = env.globals["goldenmatch_file_field_correction"]
    sql = fn(
        cluster_id=42,
        field_name="address1",
        original="1 Elm St",
        corrected="1 Elm Street, Apt 4B",
        dataset="customers",
        reason="USPS lookup",
    )
    assert "goldenmatch.correction_add" in sql
    assert "decision        => 'field_correct'" in sql
    assert "cluster_id      => 42" in sql
    assert "field_name      => 'address1'" in sql
    assert "corrected_value => '1 Elm Street, Apt 4B'" in sql
    assert "reason          => 'USPS lookup'" in sql


def test_field_correction_postgres_null_optionals() -> None:
    env = _build_env("postgres", "file_field_correction.sql")
    fn = env.globals["goldenmatch_file_field_correction"]
    sql = fn(
        cluster_id=42, field_name="address1", original=None,
        corrected="X", dataset="d",
    )
    # Optionals render as NULL, not quoted strings.
    assert "original_value  => NULL" in sql
    assert "reason          => NULL" in sql
    assert "memory_path     => NULL" in sql


def test_field_correction_duckdb_render() -> None:
    env = _build_env("duckdb", "file_field_correction.sql")
    fn = env.globals["goldenmatch_file_field_correction"]
    sql = fn(
        cluster_id=42,
        field_name="address1",
        original=None,
        corrected="1 Elm Street, Apt 4B",
        dataset="customers",
        reason=None,
    )
    assert "goldenmatch_correction_add" in sql
    assert "'field_correct'" in sql
    assert "'customers'" in sql
    # args_json embedded as a single string literal containing the JSON.
    assert '"cluster_id": 42' in sql
    assert '"field_name": "address1"' in sql


def test_field_correction_unknown_adapter_raises() -> None:
    env = _build_env("snowflake", "file_field_correction.sql")
    fn = env.globals["goldenmatch_file_field_correction"]
    with pytest.raises(RuntimeError, match="only supported on postgres and duckdb"):
        fn(
            cluster_id=1, field_name="x", original=None,
            corrected="y", dataset="d",
        )


# ---------------------------------------------------------------------------
# file_pair_correction
# ---------------------------------------------------------------------------


def test_pair_correction_postgres_approve() -> None:
    env = _build_env("postgres", "file_pair_correction.sql")
    fn = env.globals["goldenmatch_file_pair_correction"]
    sql = fn(
        id_a=42, id_b=99, decision="approve",
        dataset="customers", reason="manual review",
    )
    assert "goldenmatch.correction_add" in sql
    assert "decision      => 'approve'" in sql
    assert "id_a          => 42" in sql
    assert "id_b          => 99" in sql
    assert "reason        => 'manual review'" in sql


def test_pair_correction_invalid_decision_raises() -> None:
    env = _build_env("postgres", "file_pair_correction.sql")
    fn = env.globals["goldenmatch_file_pair_correction"]
    with pytest.raises(RuntimeError, match="decision must be"):
        fn(id_a=1, id_b=2, decision="maybe", dataset="d")


def test_pair_correction_duckdb_render() -> None:
    env = _build_env("duckdb", "file_pair_correction.sql")
    fn = env.globals["goldenmatch_file_pair_correction"]
    sql = fn(
        id_a=42, id_b=99, decision="reject",
        dataset="customers", reason=None,
    )
    assert "goldenmatch_correction_add" in sql
    assert "'reject'" in sql
    assert '"id_a": 42' in sql
    assert '"id_b": 99' in sql


def test_pair_correction_unknown_adapter_raises() -> None:
    env = _build_env("bigquery", "file_pair_correction.sql")
    fn = env.globals["goldenmatch_file_pair_correction"]
    with pytest.raises(RuntimeError, match="only supported on postgres and duckdb"):
        fn(id_a=1, id_b=2, decision="approve", dataset="d")


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
