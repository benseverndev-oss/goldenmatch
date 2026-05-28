"""Tests for v0.4 goldenmatch_dedupe materialization.

The `{% materialization %}` block itself contains dbt-specific Jinja
tags (`statement`, `make_temp_relation`, `auto_begin`, etc.) that
plain Jinja2 can't parse. We test the two HELPER macros that contain
the meaningful logic instead -- those live in
`_helpers.sql` and parse cleanly under plain Jinja2.

End-to-end materialization behavior is covered by dbt's own
integration-test harness (out-of-band -- requires running Postgres +
DuckDB targets).
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
# goldenmatch_dedupe_config_json
# ---------------------------------------------------------------------------


def test_config_json_dict_serializes() -> None:
    helpers = _load_helpers()
    result = helpers.goldenmatch_dedupe_config_json(
        {"exact": ["ssn"], "fuzzy": {"name": 0.85}},
    )
    # Should be a quoted JSON string literal.
    assert result.startswith("'")
    assert result.endswith("'")
    assert "ssn" in result
    assert "name" in result


def test_config_json_file_path_reads() -> None:
    helpers = _load_helpers()
    result = helpers.goldenmatch_dedupe_config_json("ok.yaml")
    assert result.startswith("'")
    assert "ssn" in result


def test_config_json_missing_file_errors() -> None:
    helpers = _load_helpers()
    with pytest.raises(RuntimeError, match="file not found"):
        helpers.goldenmatch_dedupe_config_json("missing.yaml")


def test_config_json_invalid_type_errors() -> None:
    helpers = _load_helpers()
    with pytest.raises(RuntimeError, match="string .file path. or dict"):
        helpers.goldenmatch_dedupe_config_json(12345)


# ---------------------------------------------------------------------------
# goldenmatch_dedupe_fn_name
# ---------------------------------------------------------------------------


def test_fn_name_golden_postgres() -> None:
    helpers = _load_helpers()
    assert helpers.goldenmatch_dedupe_fn_name("golden", "postgres") == \
        "goldenmatch.goldenmatch_dedupe_full"


def test_fn_name_golden_duckdb() -> None:
    helpers = _load_helpers()
    assert helpers.goldenmatch_dedupe_fn_name("golden", "duckdb") == \
        "goldenmatch_dedupe_full"


def test_fn_name_golden_snowflake() -> None:
    """Snowflake mirrors Postgres -- schema-qualified Snowpark Python
    UDTF in the `goldenmatch` schema."""
    helpers = _load_helpers()
    assert helpers.goldenmatch_dedupe_fn_name("golden", "snowflake") == \
        "goldenmatch.goldenmatch_dedupe_full"


def test_fn_name_clusters_postgres() -> None:
    helpers = _load_helpers()
    assert helpers.goldenmatch_dedupe_fn_name("clusters", "postgres") == \
        "goldenmatch.goldenmatch_dedupe_clusters"


def test_fn_name_pairs_postgres() -> None:
    helpers = _load_helpers()
    assert helpers.goldenmatch_dedupe_fn_name("pairs", "postgres") == \
        "goldenmatch.goldenmatch_dedupe_pairs"


def test_fn_name_clusters_duckdb_errors() -> None:
    """v0.4.0 ships clusters on Postgres only -- DuckDB raises."""
    helpers = _load_helpers()
    with pytest.raises(RuntimeError, match="clusters.*not yet implemented on DuckDB"):
        helpers.goldenmatch_dedupe_fn_name("clusters", "duckdb")


def test_fn_name_clusters_snowflake_errors() -> None:
    """v0.6 ships golden-only on Snowflake (parity with DuckDB v0.4.0)."""
    helpers = _load_helpers()
    with pytest.raises(
        RuntimeError, match="clusters.*not yet implemented on Snowflake",
    ):
        helpers.goldenmatch_dedupe_fn_name("clusters", "snowflake")


def test_fn_name_pairs_duckdb_errors() -> None:
    helpers = _load_helpers()
    with pytest.raises(RuntimeError, match="pairs.*not yet implemented on DuckDB"):
        helpers.goldenmatch_dedupe_fn_name("pairs", "duckdb")


def test_fn_name_pairs_snowflake_errors() -> None:
    helpers = _load_helpers()
    with pytest.raises(
        RuntimeError, match="pairs.*not yet implemented on Snowflake",
    ):
        helpers.goldenmatch_dedupe_fn_name("pairs", "snowflake")


def test_fn_name_invalid_output_errors() -> None:
    helpers = _load_helpers()
    with pytest.raises(RuntimeError, match="output must be one of"):
        helpers.goldenmatch_dedupe_fn_name("wat", "postgres")


def test_fn_name_unknown_adapter_errors() -> None:
    helpers = _load_helpers()
    with pytest.raises(
        RuntimeError,
        match="only supported on postgres, duckdb, and snowflake",
    ):
        helpers.goldenmatch_dedupe_fn_name("golden", "bigquery")


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
