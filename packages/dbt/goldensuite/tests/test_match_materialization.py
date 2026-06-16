"""Tests for the goldenmatch_match (two-table linkage) materialization.

Like the dedupe materialization, the `{% materialization %}` block
itself contains dbt-specific Jinja tags (`statement`,
`make_temp_relation`, `auto_begin`, etc.) that plain Jinja2 can't
parse. We test the HELPER macro that contains the meaningful logic
instead -- `goldenmatch_match_fn_name`, which lives in
`_helpers.sql` and parses cleanly under plain Jinja2.

End-to-end materialization behavior is covered by the `rust_pgrx`
psql smoke (the `goldenmatch_match_pairs` UDF) and dbt's own
integration-test harness (out-of-band -- requires a running Postgres
target).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

jinja2 = pytest.importorskip("jinja2")

_HELPERS_PATH = (
    Path(__file__).resolve().parents[1]
    / "macros" / "materializations" / "_helpers.sql"
)


class _DbtStub:
    @staticmethod
    def string_literal(s):  # noqa: ANN001
        return "'" + str(s).replace("'", "''") + "'"


class _ExceptionsStub:
    @staticmethod
    def raise_compiler_error(msg):  # noqa: ANN001
        raise RuntimeError(msg)


def _load_helpers():
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_HELPERS_PATH.parent)),
        autoescape=False,
    )
    env.globals["dbt"] = _DbtStub()
    env.globals["exceptions"] = _ExceptionsStub()
    env.globals["return"] = lambda v: v
    env.globals["tojson"] = lambda v: json.dumps(v)
    env.globals["load_file_contents"] = lambda p: (
        '{"exact": ["ssn"]}' if p == "ok.yaml" else None
    )
    template = env.get_template(_HELPERS_PATH.name)
    module = template.module
    return module


# ---------------------------------------------------------------------------
# goldenmatch_match_fn_name
# ---------------------------------------------------------------------------


def test_match_fn_name_postgres() -> None:
    helpers = _load_helpers()
    assert helpers.goldenmatch_match_fn_name("postgres") == \
        "goldenmatch.goldenmatch_match_pairs"


def test_match_fn_name_snowflake() -> None:
    """Snowflake mirrors Postgres -- schema-qualified UDF."""
    helpers = _load_helpers()
    assert helpers.goldenmatch_match_fn_name("snowflake") == \
        "goldenmatch.goldenmatch_match_pairs"


def test_match_fn_name_duckdb_errors() -> None:
    """Two-table match is Postgres-first -- DuckDB raises a compiler
    error directing callers to the JSON UDF instead."""
    helpers = _load_helpers()
    with pytest.raises(RuntimeError, match="Postgres-first"):
        helpers.goldenmatch_match_fn_name("duckdb")


def test_match_fn_name_unknown_adapter_errors() -> None:
    helpers = _load_helpers()
    with pytest.raises(RuntimeError, match="only supported on postgres"):
        helpers.goldenmatch_match_fn_name("bigquery")


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
