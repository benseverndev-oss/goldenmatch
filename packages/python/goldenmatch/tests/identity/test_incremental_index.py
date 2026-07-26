"""Index-backed incremental resolution parity (C2 slice 3, manifesto §4(ii)).

The bidirectional seam: instead of re-blocking the whole in-RAM corpus,
``resolve_record_incremental(..., blocking=cfg)`` computes the new record's
block keys, queries the PERSISTED store index for its block-mates, gathers only
those rows, and resolves against that bounded candidate frame. These tests lock
that the index path produces the SAME entity grouping as the full-``df`` path
(the byte-for-byte reference) for PK-based data, plus the self-population that
lets a later record find one just resolved.
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

# Weighted matchkey on `name` -- threshold-bearing so match_one produces scores.
MK = MatchkeyConfig(
    name="mk",
    type="weighted",
    threshold=0.8,
    fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
)
# Block on the same field the matchkey scores, so a block-mate is a candidate.
BLOCKING = BlockingConfig(
    strategy="static", keys=[BlockingKeyConfig(fields=["name"], transforms=[])]
)


def _df(rows):
    out = []
    for i, r in enumerate(rows):
        rec = {"__row_id__": i, "__source__": "src"}
        rec.update(r)
        out.append(rec)
    return pl.DataFrame(out)


def _seed_base(store, df):
    """Resolve every base row into its own singleton identity (the prior corpus)."""
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
    return [{"id": "1", "name": "Alice"}, {"id": "2", "name": "Bob"}]


def test_index_path_absorbs_like_full_df(tmp_path, base_rows):
    """A new record that matches an existing entity resolves into the SAME set
    of source records on both the full-df path and the index path."""
    df = _df(base_rows)
    incoming = {"id": "3", "name": "Alice", "__source__": "src"}

    # Reference: full-corpus path.
    full = IdentityStore(path=str(tmp_path / "full.db"))
    _seed_base(full, df)
    eid_full = resolve_record_incremental(
        incoming, df, [MK], full, run_name="stream", source_pk_col="id",
    )
    members_full = _member_ids(full, eid_full)
    full.close()

    # Index-backed path: no df passed, candidates from the persisted index.
    idx = IdentityStore(path=str(tmp_path / "idx.db"))
    _seed_base(idx, df)
    backfill_block_index(idx, df, BLOCKING, source="src", source_pk_col="id")
    eid_idx = resolve_record_incremental(
        incoming, None, [MK], idx, run_name="stream", source_pk_col="id",
        blocking=BLOCKING,
    )
    members_idx = _member_ids(idx, eid_idx)
    idx.close()

    # Same grouping: incoming Alice joins the persisted Alice, no new mint.
    assert members_full == {"src:1", "src:3"}
    assert members_idx == members_full


def test_index_path_creates_when_no_block_mate(tmp_path, base_rows):
    """A record with no block-mate creates a fresh entity on both paths."""
    df = _df(base_rows)
    incoming = {"id": "9", "name": "Zoe", "__source__": "src"}

    full = IdentityStore(path=str(tmp_path / "full.db"))
    _seed_base(full, df)
    before_full = full.count_identities()
    eid_full = resolve_record_incremental(
        incoming, df, [MK], full, run_name="stream", source_pk_col="id",
    )
    assert full.count_identities() == before_full + 1
    assert _member_ids(full, eid_full) == {"src:9"}
    full.close()

    idx = IdentityStore(path=str(tmp_path / "idx.db"))
    _seed_base(idx, df)
    backfill_block_index(idx, df, BLOCKING, source="src", source_pk_col="id")
    before_idx = idx.count_identities()
    eid_idx = resolve_record_incremental(
        incoming, None, [MK], idx, run_name="stream", source_pk_col="id",
        blocking=BLOCKING,
    )
    assert idx.count_identities() == before_idx + 1
    assert _member_ids(idx, eid_idx) == {"src:9"}
    idx.close()


def test_index_self_populates(tmp_path, base_rows):
    """A record resolved via the index registers its own block keys, so a later
    record blocking on the same key finds it as a candidate."""
    df = _df(base_rows)
    store = IdentityStore(path=str(tmp_path / "idx.db"))
    _seed_base(store, df)
    backfill_block_index(store, df, BLOCKING, source="src", source_pk_col="id")

    # First: a brand-new "Carol" (no block-mate) -> new entity, self-indexed.
    e1 = resolve_record_incremental(
        {"id": "3", "name": "Carol", "__source__": "src"}, None, [MK], store,
        run_name="s1", source_pk_col="id", blocking=BLOCKING,
    )
    # A second Carol now finds the first via the index and absorbs into it.
    e2 = resolve_record_incremental(
        {"id": "4", "name": "Carol", "__source__": "src"}, None, [MK], store,
        run_name="s2", source_pk_col="id", blocking=BLOCKING,
    )
    assert e2 == e1
    assert _member_ids(store, e1) == {"src:3", "src:4"}
    store.close()


def test_missing_df_and_blocking_raises(tmp_path):
    """Neither df nor blocking is a programming error, not a silent no-op."""
    store = IdentityStore(path=str(tmp_path / "s.db"))
    with pytest.raises(ValueError, match="df.*or.*blocking"):
        resolve_record_incremental(
            {"id": "1", "name": "Alice"}, None, [MK], store,
            source_pk_col="id",
        )
    store.close()
