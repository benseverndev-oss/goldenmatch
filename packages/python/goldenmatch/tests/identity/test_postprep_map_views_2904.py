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
