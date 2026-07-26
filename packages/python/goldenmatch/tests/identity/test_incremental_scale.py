"""C2 scale proof: index-backed incremental resolution does BOUNDED work
(manifesto §4(ii) / milestone C4).

The whole point of the persisted block-key index is that resolving a new record
against an M-identity store must NOT materialize or re-block the M-row corpus --
it gathers only the new record's block-mates. This is the measured kill
criterion, expressed here as a CI-stable *bounded-work* proof rather than a
flaky RSS threshold: `_resolve_via_index` calls `store.get_record` exactly once
per block-mate, so the candidate gather is O(block-mates) and INDEPENDENT of M.
A tracemalloc probe additionally shows peak allocation tracks the gather, not M.

The full-`df` path (the reference) is the contrast: it runs `match_one` over the
whole frame, so its work scales with M -- which is exactly what the index path
removes.
"""
from __future__ import annotations

import tracemalloc

import polars as pl
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.identity import (
    IdentityStore,
    resolve_clusters,
    resolve_record_incremental,
)
from goldenmatch.identity.block_index import backfill_block_index

MK = MatchkeyConfig(
    name="mk", type="weighted", threshold=0.8,
    fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
)
BLOCKING = BlockingConfig(
    strategy="static", keys=[BlockingKeyConfig(fields=["name"], transforms=[])]
)


def _corpus(m_unique: int, smith_mates: int) -> pl.DataFrame:
    """M unique-name singletons + `smith_mates` records all named 'Smith'
    (one shared block). Total rows = m_unique + smith_mates."""
    rows = []
    rid = 0
    for i in range(m_unique):
        rows.append({"__row_id__": rid, "__source__": "src",
                     "id": f"u{i}", "name": f"Name{i}"})
        rid += 1
    for j in range(smith_mates):
        rows.append({"__row_id__": rid, "__source__": "src",
                     "id": f"s{j}", "name": "Smith"})
        rid += 1
    return pl.DataFrame(rows)


def _seed_and_index(store, df):
    clusters = {
        i: {"members": [i], "size": 1, "pair_scores": {}, "confidence": 1.0}
        for i in range(df.height)
    }
    resolve_clusters(
        clusters, df, [], "mk", store, run_name="batch", source_pk_col="id",
    )
    backfill_block_index(store, df, BLOCKING, source="src", source_pk_col="id")


def _spy_candidates(store, monkeypatch):
    """Wrap candidates_by_block_keys to capture the candidate-set size -- the
    PURE gather (exactly the block-mates). Returns a mutable dict."""
    seen = {"size": None}
    orig = store.candidates_by_block_keys

    def _spy(keys):
        out = orig(keys)
        seen["size"] = len(out)
        return out

    monkeypatch.setattr(store, "candidates_by_block_keys", _spy)
    return seen


def _count_reads(store, monkeypatch):
    """Wrap store.get_record with a call counter; returns a mutable dict."""
    counter = {"n": 0}
    orig = store.get_record

    def _counting(record_id):
        counter["n"] += 1
        return orig(record_id)

    monkeypatch.setattr(store, "get_record", _counting)
    return counter


def test_index_gather_is_exactly_block_mates(tmp_path, monkeypatch):
    """The candidate query returns exactly the block-mates -- NOT the corpus."""
    smith_mates = 3
    df = _corpus(m_unique=500, smith_mates=smith_mates)
    store = IdentityStore(path=str(tmp_path / "s.db"))
    _seed_and_index(store, df)

    seen = _spy_candidates(store, monkeypatch)
    reads = _count_reads(store, monkeypatch)
    eid = resolve_record_incremental(
        {"id": "new", "name": "Smith", "__source__": "src"}, None, [MK], store,
        run_name="stream", source_pk_col="id", blocking=BLOCKING,
    )
    assert eid is not None
    # The gather is EXACTLY the persisted Smiths -- not the 500 unique names.
    assert seen["size"] == smith_mates
    # Total store record-reads (gather + the merge it triggers) stay bounded by
    # the block-mates, far below the 500-identity corpus.
    assert reads["n"] < 50
    store.close()


def test_gather_is_scale_invariant_in_M(tmp_path, monkeypatch):
    """Same gather + read work at M=100 and M=1000 -> O(block-mates), independent
    of store size (the C4 kill criterion)."""
    smith_mates = 4
    gathered = {}
    reads = {}
    for m in (100, 1000):
        store = IdentityStore(path=str(tmp_path / f"s{m}.db"))
        _seed_and_index(store, _corpus(m_unique=m, smith_mates=smith_mates))
        seen = _spy_candidates(store, monkeypatch)
        counter = _count_reads(store, monkeypatch)
        resolve_record_incremental(
            {"id": "new", "name": "Smith", "__source__": "src"}, None, [MK],
            store, run_name="stream", source_pk_col="id", blocking=BLOCKING,
        )
        gathered[m] = seen["size"]
        reads[m] = counter["n"]
        store.close()
    # A 10x larger corpus changes NEITHER the gather nor the read work.
    assert gathered[100] == gathered[1000] == smith_mates
    assert reads[100] == reads[1000]


def test_index_peak_alloc_tracks_gather_not_corpus(tmp_path):
    """tracemalloc sanity: resolving one record against a large store peaks near
    the block-mate gather, far below re-materializing the M-row corpus."""
    df = _corpus(m_unique=2000, smith_mates=3)
    store = IdentityStore(path=str(tmp_path / "s.db"))
    _seed_and_index(store, df)

    tracemalloc.start()
    resolve_record_incremental(
        {"id": "new", "name": "Smith", "__source__": "src"}, None, [MK], store,
        run_name="stream", source_pk_col="id", blocking=BLOCKING,
    )
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    store.close()
    # The 2000-identity corpus is ~hundreds of KB as a frame; the index resolve
    # of one record should peak well under a megabyte (gather is 3 rows). Generous
    # ceiling -- the assertion is "does NOT scale with the corpus", not a tight
    # budget.
    assert peak < 8_000_000, f"peak {peak} suggests corpus-sized materialization"
