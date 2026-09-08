"""`rowid_to_recid` / `rowid_to_pk` are views, and must read like the dicts.

`apply_batch`'s prep used to end with a pass over every prepped row that built
two more whole-frame dicts. Both duplicated values already in hand: the chosen
record_id is `_rowid_primary`'s value unless the candidate-union pre-flight
found an existing record under an ALTERNATE candidate, and the source_pk is a
pure function of that id and its source. Measured at 5M (ADR 0064) they cost
~935 MB -- the entire gap between the prep peak (8,061 MB) and the process
peak recorded during the write loop (8,996 MB).

They are now `_RecordIdView` (sparse override over `_rowid_primary`) and
`_SourcePkView` (derived on read). Nine call sites read them unchanged, so the
risk is entirely in the views answering differently from a dict. That is what
this pins: the mapping protocol each call site actually uses, the override
precedence the multi-candidate contract depends on, and -- through the real
`resolve_clusters` -- a WARM store, which is the only way `_existing_by_id` is
non-empty and the skipped loop is not skipped.
"""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.identity import IdentityStore
from goldenmatch.identity.resolve import (
    _RecordIdView,
    _SourcePkView,
    resolve_clusters,
)

_PRIMARY = {1: "src:h1:aaaaaaaaaaaa", 2: "src:h1:bbbbbbbbbbbb", 3: "other:h1:cccccccccccc"}
_SOURCES = {1: "src", 2: "src", 3: "other"}


def test_recid_view_with_empty_override_is_the_primary_dict():
    """The cold-load case: nothing resident, so the view IS `_rowid_primary`."""
    v = _RecordIdView(_PRIMARY, {})
    assert dict(v) == _PRIMARY
    assert len(v) == len(_PRIMARY)
    for k, want in _PRIMARY.items():
        assert v[k] == want
        assert v.get(k) == want
        assert k in v


def test_recid_view_misses_exactly_where_the_dict_missed():
    """Every call site guards on `.get(...) is None` or `in`. A rowid that
    never reached prep has no `_rowid_primary` entry, so it must still miss --
    an override-only hit would resurrect a row the dict form dropped."""
    v = _RecordIdView(_PRIMARY, {99: "ghost:h1:dddddddddddd"})
    assert v.get(404) is None
    assert v.get(404, "fallback") == "fallback"
    assert 404 not in v
    assert 99 not in v
    with pytest.raises(KeyError):
        v[404]


def test_recid_view_override_wins():
    """The multi-candidate contract: a pre-flight hit on an ALTERNATE candidate
    replaces the primary id. Unreachable today (every `_record_id_candidates`
    return is `(x, [x])`), which is exactly why it is pinned here rather than
    left to an integration test that cannot construct it."""
    v = _RecordIdView(_PRIMARY, {2: "src:legacy:eeeeeeeeeeee"})
    assert v[2] == "src:legacy:eeeeeeeeeeee"
    assert v.get(2) == "src:legacy:eeeeeeeeeeee"
    assert v[1] == _PRIMARY[1]


@pytest.mark.parametrize(
    ("recid", "source", "want"),
    [
        ("src:h1:aaaaaaaaaaaa", "src", "h1:aaaaaaaaaaaa"),
        ("src:12345", "src", "12345"),
        # no "{source}:" prefix -> the whole id, matching the eager `else`
        ("bare-id", "src", "bare-id"),
        # a source that is a PREFIX of the id but not followed by ":" must not
        # be stripped -- `startswith(source + ":")`, not `startswith(source)`
        ("srcery:9", "src", "srcery:9"),
        # empty source: eager form sliced [len("")+1:] == [1:]
        (":tail", "", "tail"),
    ],
)
def test_source_pk_view_matches_the_eager_slice(recid, source, want):
    v = _SourcePkView({7: recid}, {7: source})
    assert v[7] == want
    # the eager expression this replaced, verbatim
    assert v[7] == (
        recid[len(source) + 1:] if recid.startswith(f"{source}:") else recid
    )


def test_source_pk_view_follows_the_recid_override():
    """source_pk is derived from the CHOSEN id, not the primary one."""
    recids = _RecordIdView(_PRIMARY, {1: "src:legacy:ffffffffffff"})
    assert _SourcePkView(recids, _SOURCES)[1] == "legacy:ffffffffffff"


# --- integration: the warm path, where the skipped loop is not skipped -------

_N_ROWS = 60
_PER_CLUSTER = 3


def _frame() -> pl.DataFrame:
    return pl.DataFrame([
        {
            "__row_id__": c * _PER_CLUSTER + m,
            "name": f"Name{c}",
            "city": f"City{c % 7}",
            "note": None if m == 1 else f"n{c}",
        }
        for c in range(_N_ROWS // _PER_CLUSTER)
        for m in range(_PER_CLUSTER)
    ])


def _clusters() -> dict[int, dict]:
    return {
        c: {"members": [c * _PER_CLUSTER + m for m in range(_PER_CLUSTER)]}
        for c in range(_N_ROWS // _PER_CLUSTER)
    }


def _derived(store) -> list[tuple]:
    return [
        (r["record_id"], r["source"], r["source_pk"], r["record_hash"], r["payload"])
        for r in store._fetchall(
            "SELECT record_id, source, source_pk, record_hash, payload "
            "FROM source_records ORDER BY record_id", (),
        )
    ]


def test_warm_resolve_reuses_ids_and_derives_the_same_source_pks(tmp_path):
    """Second pass over the same batch: `_existing_by_id` is non-empty, so the
    pre-flight loop runs and `preflight_existing` must still be populated --
    the one behaviour the early-out could plausibly have dropped."""
    store = IdentityStore(backend="sqlite", path=str(tmp_path / "warm.db"))
    try:
        resolve_clusters(
            clusters=_clusters(), df=_frame(), store=store,
            run_name="cold", dataset="views", emit_singletons=False,
        )
        cold = _derived(store)
        cold_entities = {
            r["record_id"]: r["entity_id"]
            for r in store._fetchall(
                "SELECT record_id, entity_id FROM source_records", ()
            )
        }
        assert cold, "cold pass wrote nothing -- the test measures nothing"

        resolve_clusters(
            clusters=_clusters(), df=_frame(), store=store,
            run_name="warm", dataset="views", emit_singletons=False,
        )
        warm = _derived(store)
        warm_entities = {
            r["record_id"]: r["entity_id"]
            for r in store._fetchall(
                "SELECT record_id, entity_id FROM source_records", ()
            )
        }
    finally:
        store.close()

    assert warm == cold
    # The pre-flight found every record already resident and kept its entity,
    # rather than minting new ones -- i.e. `preflight_existing` was populated.
    assert warm_entities == cold_entities


def test_source_pk_column_matches_the_record_id_it_was_derived_from(tmp_path):
    """End-to-end shape of the derivation, read back out of the store."""
    store = IdentityStore(backend="sqlite", path=str(tmp_path / "pk.db"))
    try:
        resolve_clusters(
            clusters=_clusters(), df=_frame(), store=store,
            run_name="pk", dataset="views", emit_singletons=False,
        )
        rows = store._fetchall(
            "SELECT record_id, source, source_pk FROM source_records", ()
        )
    finally:
        store.close()

    assert rows
    for r in rows:
        rid, src, pk = r["record_id"], r["source"], r["source_pk"]
        assert pk == (rid[len(src) + 1:] if rid.startswith(f"{src}:") else rid)


# --- the ordinal collapse: one index, dense lists -----------------------------

def test_ordinal_view_reads_like_the_dict_it_replaced():
    from goldenmatch.identity.resolve import _OrdinalView

    v = _OrdinalView({10: 0, 20: 1, 30: 2}, ["a", "b", "c"])
    assert v[10] == "a" and v[30] == "c"
    assert v.get(20) == "b"
    assert dict(v) == {10: "a", 20: "b", 30: "c"}
    assert len(v) == 3
    assert 20 in v and 21 not in v
    assert v.get(21) is None
    assert v.get(21, "fallback") == "fallback"
    with pytest.raises(KeyError):
        v[21]


def test_source_view_default_and_overrides():
    from goldenmatch.identity.resolve import _SourceView

    v = _SourceView({1: 0, 2: 1, 3: 2}, "dataframe", {2: "crm"})
    assert v[1] == "dataframe"
    assert v[2] == "crm"
    assert dict(v) == {1: "dataframe", 2: "crm", 3: "dataframe"}
    assert v.get(9) is None
    with pytest.raises(KeyError):
        v[9]
    # a rowid in the override but NOT prepped must still miss, or the view
    # would resurrect a row the dict form never held
    assert 9 not in _SourceView({1: 0}, "dataframe", {9: "ghost"})


def test_repeated_row_id_overwrites_in_place(tmp_path):
    """The dict form let a repeated `__row_id__` overwrite all four values.
    The ordinal form must too -- `setdefault` returns the existing ordinal, so
    the second row writes over the first rather than appending a second entry.
    """
    df = pl.DataFrame([
        {"__row_id__": 0, "name": "First", "city": "A"},
        {"__row_id__": 0, "name": "Second", "city": "B"},   # same row id
        {"__row_id__": 1, "name": "Other", "city": "C"},
    ])
    store = IdentityStore(backend="sqlite", path=str(tmp_path / "dup.db"))
    try:
        resolve_clusters(
            clusters={0: {"members": [0, 1]}}, df=df, store=store,
            run_name="dup", dataset="views", emit_singletons=False,
        )
        rows = store._fetchall("SELECT record_id, payload FROM source_records", ())
    finally:
        store.close()

    # two distinct rowids -> two records, not three
    assert len(rows) == 2
    payloads = [r["payload"] for r in rows]
    assert any("Second" in p for p in payloads), "last write for rowid 0 must win"
    assert not any("First" in p for p in payloads), "overwritten row must not survive"


def test_multi_source_frame_keeps_per_row_source_and_pk(tmp_path):
    """`_SourceView` degenerates to a scalar only when every row agrees. A
    linkage frame carries a real `__source__`, so the override map is exercised
    and every record's source / source_pk must still be its own."""
    df = pl.DataFrame([
        {
            "__row_id__": i,
            "__source__": "crm" if i % 2 else "erp",
            "name": f"Name{i // 2}",
        }
        for i in range(12)
    ])
    store = IdentityStore(backend="sqlite", path=str(tmp_path / "multi.db"))
    try:
        resolve_clusters(
            clusters={c: {"members": [2 * c, 2 * c + 1]} for c in range(6)},
            df=df, store=store, run_name="multi", dataset="views",
            emit_singletons=False,
        )
        rows = store._fetchall(
            "SELECT record_id, source, source_pk FROM source_records", ()
        )
    finally:
        store.close()

    assert len(rows) == 12
    assert {r["source"] for r in rows} == {"crm", "erp"}
    for r in rows:
        rid, src, pk = r["record_id"], r["source"], r["source_pk"]
        assert rid.startswith(f"{src}:"), "record id must carry its own source"
        assert pk == rid[len(src) + 1:]
