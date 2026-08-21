"""The DDL identifier and the ``write_pandas`` identifier must be one string.

``ensure_schema`` emits UNQUOTED DDL, which Snowflake uppercases;
``write_pandas`` defaults to ``quote_identifiers=True``, which quotes what it is
handed VERBATIM. Before ``normalize_identifier``, the reachable default
(``IdentityStore(database="goldenmatch")``) created ``GOLDENMATCH.PUBLIC`` and
then addressed ``"goldenmatch"."PUBLIC"."_GM_STAGE_..."``, so every bulk write
failed with *object does not exist* on a real warehouse.

Two deliberate choices here, both consequences of what went wrong:

1. **Lowercase inputs.** The rest of this suite passes an already-uppercase
   ``GM``/``PUB``, which is precisely the blind spot -- it cannot produce the
   mismatch. These tests feed lowercase, the shape the shipped default produces.

2. **No fakesnow.** fakesnow is DuckDB-backed and case-INSENSITIVE, so it
   resolves the mismatched identifiers happily and would stay green either way;
   a warehouse oracle proves nothing about this bug. The invariant is a
   statement about two STRINGS, so it is asserted on the strings directly, via
   a recording fake connection. That also keeps these tests off DuckDB
   entirely -- the fakesnow-backed suites in this directory already run close
   to the DuckDB memory ceiling, and adding more real connections tipped it
   into ``OutOfMemoryException`` at fixture setup.
"""
from __future__ import annotations

import inspect
import re
from datetime import datetime

import pytest

pl = pytest.importorskip("polars")


class _FakeConn:
    """Records every statement; executes nothing.

    Deliberately exposes no ``.connection`` attribute: ``resolve_connection``
    unwraps a Snowpark ``Session`` through that name and would hand something
    else downstream, bypassing the recording.
    """

    def __init__(self):
        self.sql: list[str] = []

    def cursor(self, *_a, **_kw):
        outer = self

        class _Cur:
            def execute(self, sql, *_a, **_kw):
                outer.sql.append(sql)
                return self

            def fetchone(self):
                return None

            def fetchall(self):
                return []

            def close(self):
                return None

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        return _Cur()

    def close(self):
        return None


def _nodes_df():
    now = datetime(2026, 8, 20, 12, 0, 0)
    return pl.DataFrame(
        [{
            "entity_id": "e1", "status": "active", "merged_into": None,
            "golden_record": None, "confidence": 0.9, "dataset": "d",
            "created_at": now, "updated_at": now,
        }],
        schema={
            "entity_id": pl.Utf8, "status": pl.Utf8, "merged_into": pl.Utf8,
            "golden_record": pl.Utf8, "confidence": pl.Float64,
            "dataset": pl.Utf8, "created_at": pl.Datetime,
            "updated_at": pl.Datetime,
        },
    )


def _events_df():
    now = datetime(2026, 8, 20, 12, 0, 0)
    return pl.DataFrame(
        [{
            "entity_id": "e1", "kind": "created", "payload": None,
            "run_name": "r", "dataset": "d", "actor": "a", "trust": 1.0,
            "recorded_at": now,
        }],
        schema={
            "entity_id": pl.Utf8, "kind": pl.Utf8, "payload": pl.Utf8,
            "run_name": pl.Utf8, "dataset": pl.Utf8, "actor": pl.Utf8,
            "trust": pl.Float64, "recorded_at": pl.Datetime,
        },
    )


# --- the rule itself --------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("goldenmatch", "GOLDENMATCH"),   # the shipped IdentityStore default
        ("GOLDENMATCH", "GOLDENMATCH"),   # already folded -- idempotent
        ("PUBLIC", "PUBLIC"),
        ("public", "PUBLIC"),
        ('"mixedCase"', '"mixedCase"'),   # explicitly quoted -> untouched
        ('"x"', '"x"'),
    ],
)
def test_normalize_identifier(raw, expected) -> None:
    from goldenmatch.snowflake._store_sql import normalize_identifier

    assert normalize_identifier(raw) == expected


def test_normalize_identifier_is_idempotent() -> None:
    from goldenmatch.snowflake._store_sql import normalize_identifier

    for raw in ("goldenmatch", "PUBLIC", '"mixedCase"', "_gm_stage"):
        once = normalize_identifier(raw)
        assert normalize_identifier(once) == once


def test_the_two_constructor_defaults_agree() -> None:
    """The reachable default must not disagree with the one it overrides."""
    from goldenmatch.identity.snowflake_backend import SnowflakeIdentityStore
    from goldenmatch.identity.store import IdentityStore

    store_default = inspect.signature(
        IdentityStore.__init__
    ).parameters["database"].default
    backend_default = inspect.signature(
        SnowflakeIdentityStore.__init__
    ).parameters["database"].default
    # These had drifted ("goldenmatch" vs "GOLDENMATCH") while only the
    # IdentityStore one is reachable, so the backend's was dead and misleading.
    assert store_default == backend_default


# --- the seam: DDL identifier vs write_pandas identifier --------------------

def _resolve_unquoted(ident: str) -> str:
    """What Snowflake stores for an identifier written into unquoted DDL."""
    if len(ident) >= 2 and ident.startswith('"') and ident.endswith('"'):
        return ident
    return ident.upper()


@pytest.fixture
def bulk_run(monkeypatch):
    """Build the store on LOWERCASE names, bulk-write, return both halves.

    One fixture rather than a helper called per test, so the two write paths
    are exercised once and every assertion below reads the same recording.
    """
    import snowflake.connector.pandas_tools as pandas_tools
    from goldenmatch.identity.store import IdentityStore

    conn = _FakeConn()
    seen: list[dict] = []

    def spy(_conn, _df, table_name, **kw):
        seen.append({
            "table_name": table_name,
            "database": kw.get("database"),
            "schema": kw.get("schema"),
            "quote_identifiers": kw.get("quote_identifiers", True),
        })
        return (True, 1, 1, ())

    monkeypatch.setattr(pandas_tools, "write_pandas", spy)

    store = IdentityStore(
        backend="snowflake", connection=conn,
        # lowercase on purpose -- the shape the shipped default produces
        database="gm", schema="pub",
    )
    # A stage_and_merge caller AND the one INSERT..SELECT caller
    # (bulk_emit_events): both hand a database/schema to write_pandas, so both
    # have to obey the invariant.
    store.bulk_upsert_identities(_nodes_df())
    store.bulk_emit_events(_events_df())
    return conn, seen, store


def test_ddl_and_write_pandas_address_the_same_identifier(bulk_run) -> None:
    conn, seen, _store = bulk_run

    create = [s for s in conn.sql if s.upper().startswith("CREATE SCHEMA")]
    assert create, f"no CREATE SCHEMA recorded; saw {conn.sql[:5]}"
    m = re.search(r"CREATE SCHEMA IF NOT EXISTS\s+(\S+)\.(\S+)", create[0])
    assert m, create[0]
    ddl_db, ddl_schema = m.group(1), m.group(2)

    # Model how Snowflake RESOLVES each half rather than comparing raw strings:
    # the DDL is unquoted, so the warehouse uppercases it on the way in;
    # write_pandas quotes its argument verbatim, so what it sends resolves
    # as-is. Comparing raw strings would pass when BOTH halves are lowercase --
    # which real Snowflake still rejects.
    resolved_ddl_db = _resolve_unquoted(ddl_db)
    resolved_ddl_schema = _resolve_unquoted(ddl_schema)

    assert seen, "write_pandas was never called -- the bulk path did not run"
    for call in seen:
        # THE invariant. If these diverge, every bulk_* write raises "object
        # does not exist" on a real warehouse while fakesnow stays green.
        assert call["database"] == resolved_ddl_db, (
            f"write_pandas addressed database={call['database']!r}, which "
            f"resolves verbatim, but the unquoted DDL {ddl_db!r} resolves to "
            f"{resolved_ddl_db!r}"
        )
        assert call["schema"] == resolved_ddl_schema, (
            f"write_pandas addressed schema={call['schema']!r}, which resolves "
            f"verbatim, but the unquoted DDL {ddl_schema!r} resolves to "
            f"{resolved_ddl_schema!r}"
        )


def test_both_bulk_write_paths_were_covered(bulk_run) -> None:
    """Guards the test above: it asserts a for-loop over `seen`.

    An empty or single-entry `seen` would make it vacuous for the path that was
    not exercised, so pin that both write paths reached write_pandas.
    """
    _conn, seen, _store = bulk_run

    assert len(seen) == 2, (
        f"expected write_pandas from bulk_upsert_identities AND "
        f"bulk_emit_events, got {len(seen)}: {seen}"
    )
    stages = [c["table_name"] for c in seen]
    assert any("IDENTITY_NODES" in s for s in stages), stages
    assert any("IDENTITY_EVENTS" in s for s in stages), stages


def test_write_pandas_identifier_is_not_lowercase(bulk_run) -> None:
    """The regression itself: a lowercase name under quote_identifiers=True."""
    _conn, seen, _store = bulk_run

    for call in seen:
        assert call["quote_identifiers"] is True, (
            "this pins the connector default the fix reasons about; if it ever "
            "changes, re-read normalize_identifier's docstring"
        )
        assert call["database"] == "GM"
        assert call["schema"] == "PUB"
        # The stage table is created by unquoted DDL too, so it must also be
        # addressed uppercase.
        assert call["table_name"] == call["table_name"].upper()


def test_store_records_the_folded_names(bulk_run) -> None:
    _conn, _seen, store = bulk_run

    assert store._sf._database == "GM"
    assert store._sf._schema == "PUB"
