"""``reconcile_relationships`` on Postgres must match the SQLite reference.

The Postgres path stages the desired edge set with COPY and diffs it in SQL (a
per-row ``executemany`` of ~10M edges was most of the relationship pass at
scale). This locks its observable contract to the SQLite path: the
``(inserted, deleted, unchanged)`` counts and the final table, across inserts,
idempotent re-runs, stale deletes, last-wins duplicates, canonicalized
endpoints, skipped self/null edges, and scoping by dataset and kind.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest

pytest.importorskip("psycopg")
testing_pg = pytest.importorskip("testing.postgresql")


@pytest.fixture
def pg_url() -> Iterator[str]:
    pg = testing_pg.Postgresql()
    try:
        yield pg.url()
    finally:
        try:
            pg.stop()
        except Exception:  # Windows SIGINT teardown noise (see CLAUDE.md)
            pass


def _rows(store) -> list[tuple]:
    cols = ("entity_a_id", "entity_b_id", "kind", "field", "shared_value", "dataset")
    rows = store._fetchall(f"SELECT {', '.join(cols)} FROM identity_relationships", ())
    return sorted(tuple(r[c] for c in cols) for r in rows)


def _e(a, b, val, kind="shares_phone", field="phone", ds="d1"):
    return (a, b, kind, field, val, ds)


# Each step: (dataset, kind, desired edges). Results and table state after every
# step are compared across backends.
_SCENARIO = [
    # 1. initial load, with a reversed pair, a self edge, a null endpoint and a
    #    duplicate key whose later occurrence carries a different field (last wins)
    ("d1", "shares_phone", [
        _e("e1", "e2", "555"), _e("e3", "e2", "555"), _e("e4", "e4", "555"),
        _e(None, "e5", "555"), _e("e6", "e7", "777", field="phone_old"),
        _e("e7", "e6", "777", field="phone"),
    ]),
    # 2. idempotent re-run: nothing inserted or deleted
    ("d1", "shares_phone", [
        _e("e1", "e2", "555"), _e("e2", "e3", "555"), _e("e6", "e7", "777"),
    ]),
    # 3. another kind and another dataset: must not touch step 1's edges
    ("d1", "same_email_domain", [_e("e1", "e9", "acme.com", kind="same_email_domain",
                                    field="email")]),
    ("d2", "shares_phone", [_e("e1", "e2", "999", ds="d2")]),
    # 4. a value changed + an edge dropped: delete stale, insert new
    ("d1", "shares_phone", [_e("e1", "e2", "556"), _e("e6", "e7", "777")]),
    # 5. reconcile a kind to empty
    ("d1", "same_email_domain", []),
]


def _run(store) -> list:
    out = []
    for dataset, kind, desired in _SCENARIO:
        res = store.reconcile_relationships(dataset, kind, iter(desired))
        out.append((res, _rows(store)))
    return out


@pytest.mark.parametrize("defer_indexes", [False, True], ids=["keep-indexes", "defer-indexes"])
def test_postgres_reconcile_matches_sqlite(
    tmp_path, pg_url: str, monkeypatch, defer_indexes: bool,
) -> None:
    import goldenmatch.identity.store as store_mod
    from goldenmatch.identity.store import IdentityStore

    # defer-indexes forces the drop / insert / rebuild branch on every step that
    # inserts (the real threshold is 100k rows); it must not change the result.
    monkeypatch.setattr(store_mod, "_REL_INDEX_DEFER_MIN_ROWS", 1 if defer_indexes else 10**12)
    sqlite = IdentityStore(path=str(tmp_path / "ref.db"))
    pg = IdentityStore(backend="postgres", connection=pg_url)
    try:
        ref = _run(sqlite)
        got = _run(pg)
        indexes = {
            r["indexname"] for r in pg._fetchall(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'identity_relationships'",
                (),
            )
        }
    finally:
        sqlite.close()
        pg.close()
    assert {name for name, _ in store_mod._REL_SECONDARY_INDEXES} <= indexes
    assert [r for r, _ in ref][:2] == [(3, 0, 0), (0, 0, 3)]  # sanity on the reference
    for step, ((r_res, r_rows), (g_res, g_rows)) in enumerate(zip(ref, got), 1):
        assert g_res == r_res, f"step {step}: counts {g_res} != {r_res}"
        assert g_rows == r_rows, f"step {step}: table differs"
