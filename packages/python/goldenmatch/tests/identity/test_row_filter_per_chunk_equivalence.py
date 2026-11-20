"""Filtering identity prep per chunk must be byte-identical to copying the frame.

``identity_prep_row_filter`` used to ``filter_in`` the whole input frame down to
the rows a surviving cluster references -- a copy of every such row across every
pipeline column, resident for the whole resolve while the caller still held the
original. Per-stage current RSS at 2M records put it as the largest identity
term (+0.97 GB retained). The prep now filters each chunk to its referenced
rows instead (``GOLDENMATCH_IDENTITY_ROW_FILTER``, default ``chunk``; ``copy``
restores the whole-frame filter).

The filter only engages when some rows are NOT referenced, so the fixture mixes
clustered rows with unreferenced ones (singletons under ``emit_singletons=False``),
a null ``__row_id__`` (dropped by both forms), and a duplicated referenced id (the
last occurrence wins in both forms). Chunk sizes cut through all of it. Compared
on the deterministic surface: stored records + payloads, golden records keyed by
member set, cluster structure, and counters.
"""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.identity import IdentityStore
from goldenmatch.identity.resolve import _row_filter_per_chunk, resolve_clusters


def _rows() -> list[dict]:
    rows: list[dict] = []
    rid = 0
    for c in range(40):
        for m in range(3):                        # clustered (referenced)
            rows.append({"__row_id__": rid, "pk": f"c{c}_{m}",
                         "name": f"N{c}" if m != 1 else None, "zip": str(10000 + c)})
            rid += 1
        rows.append({"__row_id__": rid, "pk": f"s{c}",  # unreferenced singleton
                     "name": f"Solo{c}", "zip": None})
        rid += 1
    rows.append({"__row_id__": None, "pk": "orphan", "name": "x", "zip": "0"})
    rows.append({"__row_id__": 0, "pk": "c0_0", "name": "LastWins", "zip": "99999"})
    return rows


def _clusters() -> dict[int, dict]:
    out: dict[int, dict] = {}
    for c in range(40):
        base = c * 4
        out[c] = {"members": [base, base + 1, base + 2]}
        out[1000 + c] = {"members": [base + 3]}    # singleton cluster
    return out


def _snapshot(store: IdentityStore):
    recs = store._fetchall(
        "SELECT record_id, source, source_pk, record_hash, payload, entity_id "
        "FROM source_records ORDER BY record_id", ())
    derived = [(r["record_id"], r["source"], r["source_pk"], r["record_hash"], r["payload"])
               for r in recs]
    members: dict[str, set] = {}
    for r in recs:
        members.setdefault(r["entity_id"], set()).add(r["record_id"])
    nodes = store._fetchall(
        "SELECT entity_id, golden_record FROM identity_nodes WHERE status = 'active'", ())
    golden = {frozenset(members[n["entity_id"]]): n["golden_record"]
              for n in nodes if n["entity_id"] in members}
    return derived, golden, {frozenset(v) for v in members.values()}


def _run(tmp_path, tag, monkeypatch, *, mode, chunk, lane):
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_ROW_FILTER", mode)
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_PREP_CHUNK_ROWS", chunk)
    df = pl.DataFrame(_rows())
    store = IdentityStore(backend="sqlite", path=str(tmp_path / f"{tag}.db"))
    try:
        s = resolve_clusters(clusters=_clusters(), df=df.to_arrow() if lane == "arrow" else df,
                             store=store, run_name="r", dataset="rf", source_pk_col="pk",
                             emit_singletons=False)
        return vars(s).copy(), _snapshot(store)
    finally:
        store.close()


@pytest.mark.parametrize("lane", ["polars", "arrow"])
@pytest.mark.parametrize("chunk", ["7", "50", "0"], ids=lambda c: f"chunk{c}")
def test_per_chunk_filter_matches_frame_copy(tmp_path, monkeypatch, lane, chunk):
    """7 and 50 cut through referenced/unreferenced runs; 0 is the single pass."""
    base_sum, base = _run(tmp_path, f"copy{lane}{chunk}", monkeypatch,
                          mode="copy", chunk=chunk, lane=lane)
    got_sum, got = _run(tmp_path, f"chunk{lane}{chunk}", monkeypatch,
                        mode="chunk", chunk=chunk, lane=lane)
    derived, golden, struct = base
    assert derived and golden, "fixture resolved nothing -- comparison would be vacuous"
    assert not any(":s" in d[0] for d in derived), "unreferenced rows leaked into the store"
    assert got == base
    assert got_sum == base_sum
    payload_c0 = next(d[4] for d in derived if d[0].endswith(":c0_0"))
    assert "LastWins" in payload_c0               # duplicated id: last occurrence won


def test_per_chunk_mode_never_copies_the_whole_frame(tmp_path, monkeypatch):
    """Guard against the equivalence test passing because chunk mode silently
    still copies: no filter_in call may carry the full referenced-id set."""
    from goldenmatch.core import frame as F

    sizes: list[int] = []
    for cls in (F.PolarsFrame, F.ArrowFrame):
        orig = cls.filter_in

        def spy(self, col, values, _orig=orig):
            sizes.append(len(values))
            return _orig(self, col, values)

        monkeypatch.setattr(cls, "filter_in", spy)
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_PAYLOAD_PREFETCH_ROWS", "7")
    n_referenced = 40 * 3
    _run(tmp_path, "guard", monkeypatch, mode="chunk", chunk="7", lane="polars")
    assert sizes, "per-chunk filter never ran"
    assert max(sizes) < n_referenced, max(sizes)
    sizes.clear()
    _run(tmp_path, "guard_copy", monkeypatch, mode="copy", chunk="7", lane="polars")
    assert max(sizes) == n_referenced              # the copy really is the whole set


def test_row_filter_knob(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_IDENTITY_ROW_FILTER", raising=False)
    assert _row_filter_per_chunk() is True
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_ROW_FILTER", "copy")
    assert _row_filter_per_chunk() is False
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_ROW_FILTER", "CHUNK")
    assert _row_filter_per_chunk() is True
