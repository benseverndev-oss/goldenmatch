"""End-to-end parity: ``GOLDENMATCH_IDENTITY_BATCH_FINGERPRINT=1`` (batch h1)
must produce BYTE-IDENTICAL record/entity ids vs the default per-row path.

The byte-identical id invariant is the durability gate for the batch wiring --
entity ids key off the h1 hash, so any divergence splits identities across the
flag. The spy assertion (``batch_fingerprints`` is actually called when the
gate is on) is the TDD red: it fails before the wiring exists and passes after.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.identity import IdentityStore, resolve_clusters


@pytest.fixture()
def store(tmp_path):
    p = str(tmp_path / "identity.db")
    s = IdentityStore(path=p)
    yield s
    s.close()


def _df(rows):
    out = []
    for i, r in enumerate(rows):
        rec = {"__row_id__": i, "__source__": r.get("__source__", "src")}
        for k, v in r.items():
            if k.startswith("__"):
                continue
            rec[k] = v
        out.append(rec)
    return pl.DataFrame(out)


def _cluster(members, score=0.95):
    pair_scores = {}
    for i, a in enumerate(members):
        for b in members[i + 1:]:
            pair_scores[(min(a, b), max(a, b))] = score
    return {
        "members": list(members),
        "size": len(members),
        "oversized": False,
        "pair_scores": pair_scores,
        "confidence": score,
        "cluster_quality": "strong",
    }


def _mixed_df():
    """A frame mixing fully-batchable rows with a row-level-fallback row.

      - clean no-PK rows (str/int/finite-float) -> fully batchable via the
        Arrow kernel
      - one no-PK row carrying a non-finite float (``nan``) -> row-level
        fallback inside ``batch_fingerprints`` (that single row routes per-row
        while the rest go through the kernel)

    Exercises BOTH branches of ``batch_fingerprints`` in one frame; the stored
    record ids must still be byte-identical to the all-per-row path.
    """
    return pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 3],
            "__source__": ["src", "src", "src", "src"],
            "name": ["Alice", "Alyce", "Bob", "Bobby"],
            "age": [30, 31, 40, 41],
            "score": [1.0, 2.0, float("nan"), 4.0],
        }
    )


def _record_ids_for_store(store: IdentityStore) -> set[str]:
    ids: set[str] = set()
    for node in store.list_identities():
        for rec in store.get_records_for_entity(node.entity_id):
            ids.add(rec.record_id)
    return ids


def _resolve_once(store, df, clusters, pairs):
    return resolve_clusters(
        clusters, df, pairs, "wd", store, run_name="r1", source_pk_col=None,
    )


def test_batch_path_is_exercised_when_gate_on(tmp_path, monkeypatch):
    """TDD red: with the gate on, ``resolve_clusters`` must call
    ``batch_fingerprints``. Fails before the wiring (gate is a no-op)."""
    import goldenmatch.identity.resolve as resolve_mod

    calls: list[int] = []
    real = resolve_mod.batch_fingerprints

    def _spy(df):
        calls.append(df.height)
        return real(df)

    monkeypatch.setattr(resolve_mod, "batch_fingerprints", _spy)
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_BATCH_FINGERPRINT", "1")

    store = IdentityStore(path=str(tmp_path / "spy.db"))
    try:
        df = _df([{"name": "Alice"}, {"name": "Alyce"}])
        _resolve_once(store, df, {0: _cluster([0, 1])}, [(0, 1, 0.95)])
    finally:
        store.close()

    assert calls, "batch_fingerprints was not called with the gate on"


def test_batch_not_called_with_kill_switch(tmp_path, monkeypatch):
    """Kill-switch ``=0`` restores the per-row path: ``batch_fingerprints`` is
    never invoked. (Default is now ON, so this needs the explicit ``=0``.)"""
    import goldenmatch.identity.resolve as resolve_mod

    calls: list[int] = []
    real = resolve_mod.batch_fingerprints
    monkeypatch.setattr(
        resolve_mod, "batch_fingerprints",
        lambda df: (calls.append(df.height), real(df))[1],
    )
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_BATCH_FINGERPRINT", "0")

    store = IdentityStore(path=str(tmp_path / "off.db"))
    try:
        df = _df([{"name": "Alice"}, {"name": "Alyce"}])
        _resolve_once(store, df, {0: _cluster([0, 1])}, [(0, 1, 0.95)])
    finally:
        store.close()

    assert not calls, "batch_fingerprints must not run with the kill-switch =0"


def test_batch_called_by_default(tmp_path, monkeypatch):
    """Default (no env var) is ON: ``batch_fingerprints`` runs."""
    import goldenmatch.identity.resolve as resolve_mod

    calls: list[int] = []
    real = resolve_mod.batch_fingerprints
    monkeypatch.setattr(
        resolve_mod, "batch_fingerprints",
        lambda df: (calls.append(df.height), real(df))[1],
    )
    monkeypatch.delenv("GOLDENMATCH_IDENTITY_BATCH_FINGERPRINT", raising=False)

    store = IdentityStore(path=str(tmp_path / "default.db"))
    try:
        df = _df([{"name": "Alice"}, {"name": "Alyce"}])
        _resolve_once(store, df, {0: _cluster([0, 1])}, [(0, 1, 0.95)])
    finally:
        store.close()

    assert calls, "batch_fingerprints must run by default (gate default-on)"


def test_record_ids_byte_identical_batch_vs_per_row_no_pk(tmp_path, monkeypatch):
    """Mixed no-PK frame (clean + column-level-fallback dtype): the stored
    record ids must be byte-identical batch-vs-per-row."""
    df = _mixed_df()
    clusters = {0: _cluster([0, 1]), 1: _cluster([2, 3])}
    pairs = [(0, 1, 0.95), (2, 3, 0.95)]

    # Per-row (gate off).
    monkeypatch.delenv("GOLDENMATCH_IDENTITY_BATCH_FINGERPRINT", raising=False)
    s_off = IdentityStore(path=str(tmp_path / "per_row.db"))
    try:
        _resolve_once(s_off, df, clusters, pairs)
        ids_per_row = _record_ids_for_store(s_off)
    finally:
        s_off.close()

    # Batch (gate on).
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_BATCH_FINGERPRINT", "1")
    s_on = IdentityStore(path=str(tmp_path / "batch.db"))
    try:
        _resolve_once(s_on, df, clusters, pairs)
        ids_batch = _record_ids_for_store(s_on)
    finally:
        s_on.close()

    assert ids_per_row == ids_batch, (
        f"record id divergence: only per-row={ids_per_row - ids_batch} "
        f"only batch={ids_batch - ids_per_row}"
    )
    assert all(rid.startswith("src:h1:") for rid in ids_batch)


def test_record_ids_byte_identical_clean_only_no_pk(tmp_path, monkeypatch):
    """Fully-batchable clean frame (str/int) -> ids byte-identical and on the
    batch (not row-level fallback) code path."""
    df = _df([
        {"name": "Alice", "email": "a@x.com"},
        {"name": "Alyce", "email": "a@x.com"},
        {"name": "Carol", "email": "c@x.com"},
    ])
    clusters = {0: _cluster([0, 1]), 1: {"members": [2], "size": 1,
                "oversized": False, "pair_scores": {}, "confidence": 1.0}}
    pairs = [(0, 1, 0.95)]

    monkeypatch.delenv("GOLDENMATCH_IDENTITY_BATCH_FINGERPRINT", raising=False)
    s_off = IdentityStore(path=str(tmp_path / "clean_per_row.db"))
    try:
        _resolve_once(s_off, df, clusters, pairs)
        ids_per_row = _record_ids_for_store(s_off)
    finally:
        s_off.close()

    monkeypatch.setenv("GOLDENMATCH_IDENTITY_BATCH_FINGERPRINT", "1")
    s_on = IdentityStore(path=str(tmp_path / "clean_batch.db"))
    try:
        _resolve_once(s_on, df, clusters, pairs)
        ids_batch = _record_ids_for_store(s_on)
    finally:
        s_on.close()

    assert ids_per_row == ids_batch
    assert all(rid.startswith("src:h1:") for rid in ids_batch)
