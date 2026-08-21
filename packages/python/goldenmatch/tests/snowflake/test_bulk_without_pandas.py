"""The bulk paths must work with no pandas installed.

``write_pandas`` is not part of a plain ``snowflake-connector-python``: it
ships behind that package's ``[pandas]`` extra, and goldenmatch's own
``snowflake`` extra deliberately does not pull it (the runtime is pandas-free
and polars-free by design). So ``pip install goldenmatch[snowflake]`` gets a
working connector and NO pandas, and every ``bulk_*`` method has to survive
that -- it previously died with ``ModuleNotFoundError: No module named
'pandas'`` at the first bulk write.

CI is where this was caught, not a local run: the dev environment here has
pandas pulled in transitively, so the staged path was always the one measured
and the fallback never executed. These tests remove that asymmetry by forcing
the fallback explicitly, so it stays exercised in EVERY environment rather
than only in one that happens to lack pandas.

They assert equivalence, not merely absence of a crash: the fallback is what
real non-pandas installs run, so it has to land the same rows as the staged
path, not merely land some.
"""
from __future__ import annotations

from datetime import datetime

import pytest

fakesnow = pytest.importorskip("fakesnow")
pl = pytest.importorskip("polars")

_NOW = datetime(2026, 8, 20, 12, 0, 0)


@pytest.fixture
def no_pandas(monkeypatch):
    """Force every bulk path onto its no-pandas fallback.

    Patched at the single chokepoint both call sites go through, rather than
    by hiding the module from the import system: polars probes for pandas with
    ``find_spec`` and does not survive a finder that raises, so an
    import-level block takes polars down with it and the test would measure
    the wrong thing.
    """
    import goldenmatch.identity.snowflake_backend as backend
    import goldenmatch.snowflake._store_sql as store_sql

    monkeypatch.setattr(store_sql, "pandas_tools_or_none", lambda: None)
    monkeypatch.setattr(backend, "pandas_tools_or_none", lambda: None)


def _nodes_df(ids):
    return pl.DataFrame(
        [
            {
                "entity_id": eid, "status": "active", "merged_into": None,
                "golden_record": None, "confidence": 0.9, "dataset": "c",
                "created_at": _NOW, "updated_at": _NOW,
            }
            for eid in ids
        ],
        schema={
            "entity_id": pl.Utf8, "status": pl.Utf8, "merged_into": pl.Utf8,
            "golden_record": pl.Utf8, "confidence": pl.Float64,
            "dataset": pl.Utf8, "created_at": pl.Datetime,
            "updated_at": pl.Datetime,
        },
    )


def _events_df(rows):
    return pl.DataFrame(
        rows,
        schema={
            "entity_id": pl.Utf8, "kind": pl.Utf8, "payload": pl.Utf8,
            "run_name": pl.Utf8, "dataset": pl.Utf8, "actor": pl.Utf8,
            "trust": pl.Float64, "recorded_at": pl.Datetime,
        },
    )


def _event(entity_id, payload):
    return {
        "entity_id": entity_id, "kind": "created", "payload": payload,
        "run_name": "run-1", "dataset": "c", "actor": "pipeline",
        "trust": 0.7, "recorded_at": _NOW,
    }


def test_the_fallback_actually_ran(store, no_pandas) -> None:  # noqa: F811
    """Guards every test below: they would all pass on the staged path too.

    Without this, a fixture that silently failed to engage would leave the
    whole module green while measuring nothing.
    """
    from goldenmatch.snowflake import _store_sql

    assert _store_sql.pandas_tools_or_none() is None


def test_bulk_upsert_identities_works_without_pandas(store, no_pandas) -> None:  # noqa: F811
    from goldenmatch.identity.store import new_entity_id

    ids = [new_entity_id(), new_entity_id()]
    store.bulk_upsert_identities(_nodes_df(ids))

    assert store.count_identities() == 2
    for eid in ids:
        assert store.get_identity(eid) is not None


def test_fallback_is_idempotent_like_the_staged_path(store, no_pandas) -> None:  # noqa: F811
    """The fallback MERGEs, so a replay must not duplicate.

    This is the property the staged path exists to provide, and the one a
    naive row-wise INSERT fallback would silently lose: Snowflake enforces no
    PRIMARY KEY, so nothing else in the stack would catch it.
    """
    from goldenmatch.identity.store import new_entity_id

    df = _nodes_df([new_entity_id(), new_entity_id()])
    store.bulk_upsert_identities(df)
    store.bulk_upsert_identities(df)

    assert store.count_identities() == 2


def test_fallback_and_staged_path_agree(store, monkeypatch) -> None:  # noqa: F811
    """The point of the whole fallback: same rows, either way.

    Deliberately does NOT use the ``no_pandas`` fixture -- it needs BOTH
    paths in one test, so it toggles the chokepoint itself. Skips when pandas
    is absent, because then there is no staged path to compare against.
    """
    pytest.importorskip("pandas")

    import goldenmatch.snowflake._store_sql as store_sql
    from goldenmatch.identity.store import new_entity_id

    staged_id, fallback_id = new_entity_id(), new_entity_id()
    store.bulk_upsert_identities(_nodes_df([staged_id]))

    real = store_sql.pandas_tools_or_none
    assert real() is not None, "pandas is importable, so this must not be None"
    monkeypatch.setattr(store_sql, "pandas_tools_or_none", lambda: None)
    store.bulk_upsert_identities(_nodes_df([fallback_id]))

    a, b = store.get_identity(staged_id), store.get_identity(fallback_id)
    assert a is not None and b is not None
    for field_name in ("status", "confidence", "dataset", "merged_into"):
        assert getattr(a, field_name) == getattr(b, field_name), field_name


def test_bulk_emit_events_works_without_pandas(store, no_pandas) -> None:  # noqa: F811
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.bulk_upsert_identities(_nodes_df([eid]))
    store.bulk_emit_events(_events_df([_event(eid, None)]))

    hist = store.history(eid)
    assert [e.kind for e in hist] == ["created"]
    assert hist[0].actor == "pipeline"
    assert hist[0].trust == 0.7


def test_fallback_round_trips_payload_as_a_variant(store, no_pandas) -> None:  # noqa: F811
    """PARSE_JSON, not a JSON-encoded string.

    The staged path needed an explicit cast to get this right; the fallback
    builds its own INSERT and could regress it independently, so it is pinned
    here rather than inferred from the staged path's test.
    """
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.bulk_upsert_identities(_nodes_df([eid]))
    store.bulk_emit_events(_events_df([_event(eid, '{"z": 9}')]))

    assert store.history(eid)[0].payload == {"z": 9}
