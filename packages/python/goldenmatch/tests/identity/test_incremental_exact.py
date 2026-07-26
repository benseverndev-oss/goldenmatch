"""Exact-matchkey incremental resolution (C2 slice 3b, manifesto §4(ii)).

``match_one`` returns ``[]`` for exact matchkeys (``threshold is None``), so an
exact-only incremental resolve never matched anything -- the gap the manifesto
flagged. ``_exact_match_rows`` closes it: it computes the new record's matchkey
key with the SAME ``build_matchkey_expr`` the batch pipeline uses and finds
every existing row sharing that non-blank key. These tests lock exact-key
absorb / create on BOTH the full-``df`` path and the index-backed path, plus the
blank-key and transform semantics.
"""
from __future__ import annotations

import polars as pl
import pytest
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
from goldenmatch.identity.resolve import _exact_match_rows

# Exact matchkey on `email` (threshold None -> match_one skips it).
EXACT_MK = MatchkeyConfig(
    name="email_exact", type="exact", fields=[MatchkeyField(field="email")]
)
# Exact matchkey with a lowercase transform (keys must fold case).
EXACT_MK_LC = MatchkeyConfig(
    name="email_lc", type="exact",
    fields=[MatchkeyField(field="email", transforms=["lowercase"])],
)
# Block on email so exact block-mates are candidates on the index path.
BLOCKING = BlockingConfig(
    strategy="static", keys=[BlockingKeyConfig(fields=["email"], transforms=[])]
)


def _df(rows):
    out = []
    for i, r in enumerate(rows):
        rec = {"__row_id__": i, "__source__": "src"}
        rec.update(r)
        out.append(rec)
    return pl.DataFrame(out)


def _seed_base(store, df):
    clusters = {
        i: {"members": [i], "size": 1, "pair_scores": {}, "confidence": 1.0}
        for i in range(df.height)
    }
    resolve_clusters(
        clusters, df, [], "mk", store, run_name="batch", source_pk_col="id",
    )


def _member_ids(store, eid):
    return {r.record_id for r in store.get_records_for_entity(eid)}


@pytest.fixture()
def base_rows():
    return [
        {"id": "1", "email": "alice@x.com"},
        {"id": "2", "email": "bob@x.com"},
    ]


# --- _exact_match_rows unit behaviour --------------------------------------

def test_exact_match_rows_finds_shared_key():
    df = _df([{"id": "1", "email": "a@x.com"}, {"id": "2", "email": "b@x.com"}])
    hits = _exact_match_rows({"email": "a@x.com"}, df, EXACT_MK)
    assert hits == {0: 1.0}


def test_exact_match_rows_blank_key_matches_nothing():
    """Two records both missing the field are NOT an exact match (the pipeline's
    filter_nonblank_key invariant -- else every blank joins and clusters explode)."""
    df = _df([{"id": "1", "email": ""}, {"id": "2", "email": "b@x.com"}])
    assert _exact_match_rows({"email": ""}, df, EXACT_MK) == {}
    assert _exact_match_rows({"email": None}, df, EXACT_MK) == {}


def test_exact_match_rows_applies_transforms():
    """The record key folds through the matchkey transform, so case-skewed values
    share one key."""
    df = _df([{"id": "1", "email": "Alice@X.com"}])
    hits = _exact_match_rows({"email": "alice@x.com"}, df, EXACT_MK_LC)
    assert hits == {0: 1.0}
    # Without the transform the same pair does NOT exact-match.
    assert _exact_match_rows({"email": "alice@x.com"}, df, EXACT_MK) == {}


def test_exact_match_rows_missing_field_no_match():
    df = _df([{"id": "1", "email": "a@x.com"}])
    assert _exact_match_rows({"name": "Alice"}, df, EXACT_MK) == {}


# --- full-df incremental path ----------------------------------------------

def test_full_df_absorbs_on_exact_email(tmp_path, base_rows):
    store = IdentityStore(path=str(tmp_path / "s.db"))
    df = _df(base_rows)
    _seed_base(store, df)
    alice = store.find_entity_by_record("src:1")
    before = store.count_identities()
    eid = resolve_record_incremental(
        {"id": "3", "email": "alice@x.com", "__source__": "src"}, df, [EXACT_MK],
        store, run_name="stream", source_pk_col="id",
    )
    assert eid == alice
    assert store.count_identities() == before  # absorbed, no new mint
    assert _member_ids(store, eid) == {"src:1", "src:3"}
    store.close()


def test_full_df_creates_when_no_exact_match(tmp_path, base_rows):
    store = IdentityStore(path=str(tmp_path / "s.db"))
    df = _df(base_rows)
    _seed_base(store, df)
    before = store.count_identities()
    eid = resolve_record_incremental(
        {"id": "9", "email": "zoe@x.com", "__source__": "src"}, df, [EXACT_MK],
        store, run_name="stream", source_pk_col="id",
    )
    assert store.count_identities() == before + 1
    assert _member_ids(store, eid) == {"src:9"}
    store.close()


# --- index-backed incremental path -----------------------------------------

def test_index_path_absorbs_on_exact_email(tmp_path, base_rows):
    """Same absorb outcome on the index path (candidates from the block index)
    as the full-df path -- exact matchkeys now work incrementally end to end."""
    df = _df(base_rows)
    incoming = {"id": "3", "email": "alice@x.com", "__source__": "src"}

    full = IdentityStore(path=str(tmp_path / "full.db"))
    _seed_base(full, df)
    eid_full = resolve_record_incremental(
        incoming, df, [EXACT_MK], full, run_name="stream", source_pk_col="id",
    )
    members_full = _member_ids(full, eid_full)
    full.close()

    idx = IdentityStore(path=str(tmp_path / "idx.db"))
    _seed_base(idx, df)
    backfill_block_index(idx, df, BLOCKING, source="src", source_pk_col="id")
    eid_idx = resolve_record_incremental(
        incoming, None, [EXACT_MK], idx, run_name="stream", source_pk_col="id",
        blocking=BLOCKING,
    )
    members_idx = _member_ids(idx, eid_idx)
    idx.close()

    assert members_full == {"src:1", "src:3"}
    assert members_idx == members_full
