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


# ----------------------------------------------------------------------
# #381 -- _read_all unifies schema across chunks before staging
# ----------------------------------------------------------------------


def test_read_all_unifies_dtype_drift_across_chunks():
    """#381: when chunk1 infers Null for an all-NULL column and chunk2
    infers Int64 (or any other concrete type), _read_all must NOT let
    that drift propagate to the staging parquet files. The prior
    behavior (post-#379) failed at read-back with:

        data type mismatch for column dm_npi:
        incoming: Null != target: Int64

    Fix: first non-empty chunk seeds the unified schema; subsequent
    chunks cast columns to it before write.
    """
    from collections.abc import Iterator

    from goldenmatch.db.connector import DatabaseConnector
    from goldenmatch.db.sync import _read_all

    class _DriftyConnector(DatabaseConnector):
        """Chunk 1: dm_npi all-NULL (Polars infers Null dtype).
        Chunk 2: dm_npi has Int64 values."""

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

    chunk1 = pl.DataFrame(
        {"id": [1, 2, 3], "dm_npi": [1234567890, 9876543210, 5555555555]},
    )
    chunk2 = pl.DataFrame(
        {"id": [4, 5], "dm_npi": [None, None]},
        schema={"id": pl.Int64, "dm_npi": pl.Null},  # the drift case
    )
    connector = _DriftyConnector([chunk1, chunk2])

    # Without the schema-unify fix, this raised at the pl.read_parquet
    # multi-file step. With the fix, the read-back succeeds and the
    # resulting frame keeps Int64 dtype for dm_npi.
    df = _read_all(connector, "ignored", chunk_size=10)
    assert df.height == 5
    assert df.schema["dm_npi"] == pl.Int64, (
        f"dm_npi should retain Int64 dtype from the first chunk; got {df.schema['dm_npi']}"
    )


# ----------------------------------------------------------------------
# #384 -- LazyFrame thread-through avoids double materialization
# ----------------------------------------------------------------------


def test_read_all_lazy_returns_lazyframe_not_eager():
    """#384: the full-scan path uses _read_all_lazy and threads a
    LazyFrame through __source__/__row_id__/matchkey computation,
    materializing ONCE inside _full_scan_pipeline. Previously the
    eager `_read_all` materialized in the read step AND the matchkey
    pipeline did a second collect -- peak RSS ~2x the frame size during
    that second collect, OOMing an 8 GB sandbox at 1.13M rows.
    """
    from collections.abc import Iterator

    from goldenmatch.db.connector import DatabaseConnector
    from goldenmatch.db.sync import _read_all_lazy

    class _Conn(DatabaseConnector):
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

    from pathlib import Path

    chunks = [pl.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})]
    result = _read_all_lazy(_Conn(chunks), "ignored", chunk_size=10)
    assert isinstance(result, tuple) and len(result) == 2
    lf, staging = result
    assert lf is not None
    assert isinstance(lf, pl.LazyFrame), (
        f"_read_all_lazy should return LazyFrame; got {type(lf).__name__}"
    )
    assert isinstance(staging, Path)
    # Staging files exist while caller is still iterating; collect proves
    # the lazy scan actually reads them.
    assert lf.collect().height == 3
    import shutil  # noqa: PLC0415
    shutil.rmtree(staging, ignore_errors=True)

    # Empty-input case returns (None, None) so caller can early-exit.
    assert _read_all_lazy(_Conn([]), "ignored", chunk_size=10) == (None, None)


def test_run_sync_full_scan_uses_lazyframe_path():
    """#384 structural guard: run_sync's full-scan branch must call
    _read_all_lazy (not _read_all) so the read step doesn't materialize
    the full frame before _full_scan_pipeline can chain matchkeys onto
    the lazy plan.
    """
    import inspect

    from goldenmatch.db import sync

    src = inspect.getsource(sync.run_sync)
    code = "\n".join(
        ln for ln in src.splitlines() if not ln.lstrip().startswith("#")
    )
    assert "_read_all_lazy" in code, (
        "run_sync's full-scan path should call _read_all_lazy. See #384."
    )


def test_read_all_lazy_staging_survives_lazyframe_rebinding():
    """#388: the staging dir must NOT be tied to the LazyFrame's Python
    object lifetime. When a caller does

        lf = _read_all_lazy(...)
        lf = lf.with_columns(...)  # new LazyFrame; original GC'd

    the original LazyFrame's Python wrapper is GC'd while the underlying
    scan_parquet node lives on in the derived frame's plan. If staging
    cleanup is triggered by the wrapper's GC, the parquet files vanish
    before the eventual .collect() can read them.

    Repro: read into _read_all_lazy, do a chain of with_columns,
    explicitly GC any intermediate references, then collect. Must
    return the right row count (staging files still on disk).
    """
    import gc
    import shutil  # noqa: PLC0415
    from collections.abc import Iterator

    from goldenmatch.db.connector import DatabaseConnector
    from goldenmatch.db.sync import _read_all_lazy

    class _Conn(DatabaseConnector):
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

    chunks = [pl.DataFrame({"id": [1, 2, 3], "x": [10, 20, 30]})]
    lf, staging = _read_all_lazy(_Conn(chunks), "ignored", chunk_size=10)
    try:
        # Chain that rebinds lf to a derived frame; the original's
        # Python wrapper becomes eligible for GC. Force a cycle to
        # surface any weakref-style early-cleanup bug deterministically.
        lf = lf.with_columns(pl.lit("new").alias("__source__"))
        lf = lf.with_row_index("__row_id__")
        gc.collect()  # any premature staging-dir delete would fire here
        df = lf.collect()
        assert df.height == 3
        assert "__source__" in df.columns and "__row_id__" in df.columns
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def test_full_scan_pipeline_drops_existing_row_id_before_dedupe_df():
    """#394: run_sync attaches __row_id__ + __source__ to the LazyFrame
    before calling _full_scan_pipeline. dedupe_df adds its own
    __row_id__ unconditionally via _add_row_ids, so passing a frame that
    already has the column raises "duplicate column name __row_id__"
    from Polars' with_columns step.

    Repro: hand _full_scan_pipeline a frame that already has both
    bookkeeping columns. It must dispatch to dedupe_df successfully
    instead of raising.
    """
    from unittest.mock import MagicMock, patch

    from goldenmatch._api import DedupeResult
    from goldenmatch.config.schemas import (
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    from goldenmatch.db.sync import _full_scan_pipeline

    df = pl.DataFrame({
        "name": ["alice", "alice", "bob", "carol"],
        "city": ["nyc", "nyc", "la", "sf"],
        "__source__": ["new"] * 4,
        "__row_id__": [0, 1, 2, 3],
    })

    config = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="exact_name", type="exact",
            fields=[MatchkeyField(field="name")],
        )],
    )

    fake_result = DedupeResult(
        golden=None,
        clusters={},
        dupes=None,
        unique=None,
        stats={},
        scored_pairs=[],
    )

    connector = MagicMock()
    with patch("goldenmatch._api.dedupe_df", return_value=fake_result) as mock_dedupe:
        _full_scan_pipeline(
            connector, df, "src_table", config, config.get_matchkeys(),
            "separate", True, "run_1", "cfg_hash", 4,
        )

    passed_df = mock_dedupe.call_args[0][0]
    assert "__row_id__" not in passed_df.columns, (
        "_full_scan_pipeline must strip __row_id__ before dispatching to "
        "dedupe_df -- dedupe_df re-adds it internally. See #394."
    )
    assert "__source__" not in passed_df.columns, (
        "_full_scan_pipeline must strip __source__ before dispatching to "
        "dedupe_df -- dedupe_df re-adds it internally. See #394."
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
