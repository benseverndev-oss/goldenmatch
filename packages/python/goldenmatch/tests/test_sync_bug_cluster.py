"""Regression tests for the `goldenmatch sync` bug cluster (#362-#365).

Each test pins one of the four bugs that surfaced together from a single
sync run against a 1.13M-row Postgres table on a slim Python build.
"""

from __future__ import annotations

import builtins
import sys

import polars as pl
import pytest  # noqa: F401 -- used via __main__ block

# ----------------------------------------------------------------------
# #364 -- lazy sqlite3 import in ReviewQueue
# ----------------------------------------------------------------------


def test_goldenmatch_imports_without_sqlite3(monkeypatch):
    """#364: top-level imports must not require _sqlite3.

    Simulates a slim Python build (Vercel Sandbox, minimal Docker)
    where the sqlite3 stdlib module is missing.
    """
    # Stash cached modules so we can force a fresh import chain.
    cached = {
        k: v for k, v in list(sys.modules.items())
        if k.startswith(("goldenmatch", "sqlite3"))
    }
    for k in list(cached):
        del sys.modules[k]

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sqlite3" or name.startswith("sqlite3."):
            raise ModuleNotFoundError("No module named '_sqlite3'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    try:
        import goldenmatch  # noqa: F401 -- must not raise
        from goldenmatch.cli.main import app  # noqa: F401 -- CLI entry must not raise
        assert app is not None
    finally:
        # Restore the cached modules so other tests see a normal env.
        monkeypatch.setattr(builtins, "__import__", real_import)
        for k in list(sys.modules):
            if k.startswith("goldenmatch"):
                del sys.modules[k]
        for k, v in cached.items():
            sys.modules[k] = v


# ----------------------------------------------------------------------
# #365 -- _quote_ident splits schema.table
# ----------------------------------------------------------------------


def test_quote_ident_splits_schema_and_table():
    """#365: schema.table must quote as 'schema'.'table', not 'schema.table'."""
    from goldenmatch.db.connector import _quote_ident

    assert _quote_ident("gm.pubrecord_pub1") == '"gm"."pubrecord_pub1"'
    assert _quote_ident("pubrecord_pub1") == '"pubrecord_pub1"'
    # Double-quote escape (existing behavior, must not regress).
    assert _quote_ident('weird"name') == '"weird""name"'
    assert _quote_ident('gm.weird"name') == '"gm"."weird""name"'


# ----------------------------------------------------------------------
# #363 -- chunked Postgres reads cast Null-dtype columns to Utf8
# ----------------------------------------------------------------------


def test_concat_chunks_with_null_dtype_column_promotes_to_utf8():
    """#363: chunks where a column is all-NULL in the first chunk and
    has String values in a later chunk must concat cleanly. Without the
    normalization, ``pl.concat`` raises 'type String is incompatible
    with expected type Null'.
    """
    from goldenmatch.db.connector import _normalize_chunk_schema

    chunk_all_null = pl.DataFrame({
        "id": [1, 2, 3],
        "sparse_col": [None, None, None],
    })
    chunk_with_values = pl.DataFrame({
        "id": [4, 5],
        "sparse_col": ["foo", "bar"],
    })

    normalized = [
        _normalize_chunk_schema(chunk_all_null),
        _normalize_chunk_schema(chunk_with_values),
    ]
    out = pl.concat(normalized)
    assert out.height == 5
    assert out.schema["sparse_col"] == pl.Utf8


# ----------------------------------------------------------------------
# #362 -- replace top_k_by(reverse=...) with sort_by(descending=...)
# ----------------------------------------------------------------------


def test_most_complete_strategy_uses_sort_by_not_top_k_by():
    """#362: the most-complete golden record builder must avoid
    ``top_k_by(..., reverse=False)`` because newer Polars versions don't
    accept ``reverse`` as a kwarg and silently mis-bind the args.

    Structural check: the polars-native builder source should use
    ``sort_by(...).first()`` not ``top_k_by(...)``.
    """
    import inspect

    from goldenmatch.core import golden

    src = inspect.getsource(golden._build_golden_records_polars_native)
    # Strip comment lines so the historical-context comment can stay.
    code_lines = [
        ln for ln in src.splitlines()
        if not ln.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines)
    assert "top_k_by(" not in code, (
        "top_k_by(by=..., k=1, reverse=False) breaks on newer Polars; "
        "use sort_by(col, descending=True).first() instead. See #362."
    )
    assert "sort_by(" in code


def test_most_complete_picks_longest_string():
    """End-to-end: the most-complete strategy must still pick the
    longest non-null string per cluster after the top_k_by -> sort_by
    rewrite.
    """
    from goldenmatch.config.schemas import GoldenRulesConfig
    from goldenmatch.core.golden import build_golden_records_batch

    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 1, 2, 2],
        "__row_id__": [0, 1, 2, 3, 4],
        "name": ["Bob", "Robert", "Rob", "Alice", "Al"],
    })
    rules = GoldenRulesConfig(default_strategy="most_complete")
    out = build_golden_records_batch(df, rules)
    by_cluster = {}
    for r in out:
        v = r["name"]
        # build_golden_records_batch wraps each field as {value, confidence}
        if isinstance(v, dict):
            v = v.get("value")
        by_cluster[r["__cluster_id__"]] = v
    assert by_cluster[1] == "Robert"  # longest among Bob/Robert/Rob
    assert by_cluster[2] == "Alice"   # longest among Alice/Al


# ----------------------------------------------------------------------
# #368 -- psycopg3 migration + server-side cursor for streaming reads
# ----------------------------------------------------------------------


def test_postgres_connector_imports_psycopg3_not_psycopg2():
    """#368: PostgresConnector must require psycopg3, matching the
    [postgres] extra. The legacy psycopg2 path was the root cause of
    the install regression on slim Python builds + the OOM on 1.13M
    row reads (psycopg2's client-side cursor buffered the full result
    set before fetchmany could iterate).
    """
    import inspect

    from goldenmatch.db import connector

    src = inspect.getsource(connector)
    assert "import psycopg2" not in src, (
        "PostgresConnector still imports psycopg2; the [postgres] extra "
        "ships psycopg3 only since the Phase 6 IdentityStore migration. See #368."
    )
    assert "import psycopg" in src
    # Error message should also reference psycopg3 (the actual extra-installed driver).
    assert "psycopg3" in src or "psycopg[binary]" in src


def test_postgres_read_table_uses_server_side_cursor():
    """#368: read_table must declare a server-side cursor (named cursor)
    so Postgres streams rows from a portal instead of psycopg buffering
    them all client-side. Required to read a 1.13M-row table inside a
    16 GB sandbox without OOM.
    """
    import inspect

    from goldenmatch.db.connector import PostgresConnector

    src = inspect.getsource(PostgresConnector.read_table)
    # A named cursor (name="...") is what asks psycopg3 to use a server-
    # side portal. Without a name, the default cursor caches results in
    # the connection buffer regardless of fetchmany() size.
    assert 'name="gm_sync_read"' in src or "name='gm_sync_read'" in src, (
        "read_table should open a named server-side cursor; without it the "
        "1.13M-row read OOMs the client process before fetchmany iterates. See #368."
    )


# ----------------------------------------------------------------------
# #378 -- _read_all stages chunks to disk, avoids double materialization
# ----------------------------------------------------------------------


def test_read_all_stages_chunks_to_disk_not_python_list():
    """#378: _read_all must not accumulate chunks in a Python list +
    pl.concat them -- that doubles peak memory during the read step.
    On a 1.13M-row x 58-col Postgres view the prior behavior hit 6.6 GB
    peak, OOMing an 8 GB sandbox.

    Structural guard: the source should NOT contain `chunks = []` /
    `chunks.append(chunk)` / `pl.concat(chunks` patterns. It should
    stage to a tempfile parquet via `write_parquet` and read back via
    `read_parquet`.
    """
    import inspect

    from goldenmatch.db import sync

    src = inspect.getsource(sync._read_all)
    # Strip comment lines so historical-context comments can mention
    # the old behavior without tripping the guard.
    code_lines = [
        ln for ln in src.splitlines()
        if not ln.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines)

    assert "chunks.append" not in code, (
        "_read_all should not accumulate chunks in a Python list -- "
        "stream chunks to a temp parquet instead. See #378."
    )
    assert "pl.concat(chunks" not in code, (
        "_read_all should not pl.concat a list of chunks -- that "
        "doubles peak memory during the read step. See #378."
    )
    assert "write_parquet" in code and "read_parquet" in code, (
        "_read_all should stage chunks to a temp parquet via "
        "write_parquet and read them back via read_parquet. See #378."
    )


def test_read_all_round_trips_data_from_streaming_connector():
    """End-to-end smoke: _read_all should still return a DataFrame
    with the right rows / cols when the connector streams in chunks.
    """
    from collections.abc import Iterator

    from goldenmatch.db.connector import DatabaseConnector
    from goldenmatch.db.sync import _read_all

    class _FakeConnector(DatabaseConnector):
        """Yields three pre-built chunks; ignores chunk_size."""

        def __init__(self, chunks: list[pl.DataFrame]):
            self._chunks = chunks

        def connect(self) -> None: ...
        def close(self) -> None: ...
        def read_table(
            self, table: str, chunk_size: int = 10000,
        ) -> Iterator[pl.DataFrame]:
            yield from self._chunks
        def read_query(self, query: str) -> pl.DataFrame:
            return pl.DataFrame()
        def write_dataframe(self, df, table, mode="append") -> int:
            return 0
        def execute(self, sql, params=None) -> None: ...
        def table_exists(self, table: str) -> bool:
            return False
        def get_row_count(self, table: str) -> int:
            return 0

    chunks = [
        pl.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]}),
        pl.DataFrame({"id": [4, 5], "name": ["d", "e"]}),
        pl.DataFrame({"id": [6], "name": ["f"]}),
    ]
    connector = _FakeConnector(chunks)
    df = _read_all(connector, "ignored", chunk_size=10)
    assert df.height == 6
    assert sorted(df["id"].to_list()) == [1, 2, 3, 4, 5, 6]
    assert sorted(df["name"].to_list()) == ["a", "b", "c", "d", "e", "f"]


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
