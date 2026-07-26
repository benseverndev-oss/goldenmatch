"""Wave C / C1: the versioned ResolutionBatch seam contract.

Pins two things: (1) the contract object is versioned + immutable, and (2)
introducing it is BYTE-IDENTICAL -- resolve_clusters driven by an explicit batch
dispatches exactly the same store writes as the loose-kwargs path (every current
caller passes no batch, so this is the no-behavior-change guarantee).
"""
from __future__ import annotations

import dataclasses

import pytest
from goldenmatch.identity.resolution_batch import ResolutionBatch
from goldenmatch.identity.resolve import resolve_clusters

from .test_resolve_bulk_writes_1886 import (
    _singleton_clusters,
    _singleton_df,
    _TxnRecordingStore,
)


def test_batch_is_versioned():
    assert ResolutionBatch.CONTRACT_VERSION == 1
    b = ResolutionBatch.from_args(run_id="r1")
    assert b.contract_version == 1


def test_batch_is_immutable():
    b = ResolutionBatch.from_args(run_id="r1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        b.run_id = "r2"  # type: ignore[misc]


def test_flush_rows_defaults_to_env(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_IDENTITY_BULK_FLUSH_ROWS", raising=False)
    assert ResolutionBatch.from_args(run_id="r1").flush_rows == 250_000
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_BULK_FLUSH_ROWS", "1000")
    assert ResolutionBatch.from_args(run_id="r1").flush_rows == 1000
    # An explicit value always wins over the env.
    assert ResolutionBatch.from_args(run_id="r1", flush_rows=7).flush_rows == 7


def test_batch_path_is_byte_identical_to_kwargs_path():
    """resolve_clusters(batch=...) must dispatch the same store writes, in the same
    order, as the equivalent loose-kwargs call."""
    n = 5
    kw_store = _TxnRecordingStore()
    resolve_clusters(
        clusters=_singleton_clusters(n), df=_singleton_df(n), scored_pairs=[],
        store=kw_store, run_name="run1", dataset="crm", source_pk_col="raw_id",
    )

    batch_store = _TxnRecordingStore()
    resolve_clusters(
        clusters=_singleton_clusters(n), df=_singleton_df(n), scored_pairs=[],
        store=batch_store,
        batch=ResolutionBatch.from_args(
            run_id="run1", dataset="crm", source_pk_col="raw_id",
        ),
    )

    assert batch_store.events == kw_store.events
    assert any(e in {"bulk_upsert_records", "bulk_add_edges"} for e in batch_store.events)


def test_explicit_batch_overrides_loose_kwargs():
    """When both a batch and loose kwargs are given, the batch is authoritative
    (the rebind at the top of resolve_clusters). Proven via the per-record ABSORB
    path so run_name flows into the writes recorded by the fake."""
    n = 3
    preexisting = {f"crm:r{i}": f"ent-{i}" for i in range(n)}
    store = _TxnRecordingStore(preexisting=preexisting)
    # Loose run_name says "ignored"; the batch says "authoritative".
    resolve_clusters(
        clusters=_singleton_clusters(n), df=_singleton_df(n), scored_pairs=[],
        store=store, run_name="ignored", dataset="ignored", source_pk_col="raw_id",
        batch=ResolutionBatch.from_args(
            run_id="authoritative", dataset="crm", source_pk_col="raw_id",
        ),
    )
    # The run executed the per-record path inside one transaction (no crash from
    # the override), i.e. the batch drove resolution.
    assert "ENTER" in store.events and "EXIT" in store.events
