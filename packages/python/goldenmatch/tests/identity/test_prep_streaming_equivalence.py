"""`apply_batch`'s streamed prep must be byte-identical to the single pass.

The prep used to materialize EVERY row as a Python dict up front and hold that
list for the whole derivation loop. At 5M that list is ~3.9 GB of the 10.4 GB
apply_batch adds (ADR 0064), plus ~0.6 GB for a whole-frame `h1_by_rowid` --
yet each row is read exactly once. It now streams in chunks
(`GOLDENMATCH_IDENTITY_PREP_CHUNK_ROWS`, 0 = legacy single pass).

Chunking is only safe because the per-row derived values do not depend on the
frame they were computed in: chunks are contiguous slices in the original
order, and `batch_fingerprints` is documented per-row. This asserts that
rather than trusting it -- across a dataset spanning MANY chunks, with a chunk
size that deliberately does not divide the row count evenly.

Entity ids are uuid4 and legitimately differ between runs, so the comparison
is on the deterministic surface: every source_record's derived values, and the
resolved cluster STRUCTURE (record ids grouped by entity, compared as a set of
frozensets).
"""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.identity import IdentityStore
from goldenmatch.identity.resolve import resolve_clusters

_N_ROWS = 900          # spans multiple chunks below
_PER_CLUSTER = 3


def _frame() -> pl.DataFrame:
    rows = []
    for c in range(_N_ROWS // _PER_CLUSTER):
        for m in range(_PER_CLUSTER):
            rows.append({
                "__row_id__": c * _PER_CLUSTER + m,
                "name": f"Name{c}",
                "city": f"City{c % 17}",
                # a null and an empty string in the mix: both flow through
                # _row_to_payload / the canonical fingerprint differently
                "note": None if m == 1 else f"n{c}",
                "blank": "",
            })
    return pl.DataFrame(rows)


def _clusters() -> dict[int, dict]:
    out: dict[int, dict] = {}
    for c in range(_N_ROWS // _PER_CLUSTER):
        members = [c * _PER_CLUSTER + m for m in range(_PER_CLUSTER)]
        out[c] = {"members": members}
    return out


def _resolve_into(tmp_path, tag: str, monkeypatch, chunk: str) -> tuple[dict, list, set]:
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_PREP_CHUNK_ROWS", chunk)
    store = IdentityStore(backend="sqlite", path=str(tmp_path / f"{tag}.db"))
    try:
        summary = resolve_clusters(
            clusters=_clusters(), df=_frame(), store=store,
            run_name=f"run_{tag}", dataset="equiv", emit_singletons=False,
        )
        recs = store._fetchall(
            "SELECT record_id, source, source_pk, record_hash, payload, entity_id "
            "FROM source_records ORDER BY record_id", (),
        )
        derived = [
            (r["record_id"], r["source"], r["source_pk"], r["record_hash"], r["payload"])
            for r in recs
        ]
        groups: dict[str, set] = {}
        for r in recs:
            groups.setdefault(r["entity_id"], set()).add(r["record_id"])
        structure = {frozenset(v) for v in groups.values()}
        return vars(summary).copy(), derived, structure
    finally:
        store.close()


@pytest.mark.parametrize("chunk", ["7", "128", "899", "901"], ids=lambda c: f"chunk{c}")
def test_streamed_prep_matches_single_pass(tmp_path, monkeypatch, chunk):
    """Chunk sizes that divide unevenly, exceed, and fall just short of N."""
    base_summary, base_derived, base_structure = _resolve_into(
        tmp_path, f"legacy{chunk}", monkeypatch, "0"
    )
    chunk_summary, chunk_derived, chunk_structure = _resolve_into(
        tmp_path, f"chunked{chunk}", monkeypatch, chunk
    )

    assert base_derived, "fixture resolved nothing -- the test would be vacuous"
    # Every derived per-record value is identical, in the same order.
    assert chunk_derived == base_derived
    # And the records group into entities the same way (ids themselves are uuid4).
    assert chunk_structure == base_structure
    # Counters agree too.
    assert chunk_summary == base_summary


def test_chunking_actually_engages(tmp_path, monkeypatch):
    """Guard against the parametrised test passing because chunking silently
    no-ops: a chunk size of 7 over 900 rows must take many slices, so if the
    env var were ignored this test still passes -- but the equivalence test
    above would then be comparing the single pass against itself. Assert the
    knob is actually read."""
    from goldenmatch.identity.resolve import _prep_chunk_rows

    monkeypatch.setenv("GOLDENMATCH_IDENTITY_PREP_CHUNK_ROWS", "7")
    assert _prep_chunk_rows() == 7
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_PREP_CHUNK_ROWS", "0")
    assert _prep_chunk_rows() == 0          # kill-switch -> single pass
    monkeypatch.delenv("GOLDENMATCH_IDENTITY_PREP_CHUNK_ROWS")
    assert _prep_chunk_rows() == 250_000    # default is ON
