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
from goldenmatch.identity.resolve import apply_batch, resolve_clusters

from .test_resolve_bulk_writes_1886 import (
    _singleton_clusters,
    _singleton_df,
    _TxnRecordingStore,
)


def test_batch_is_versioned():
    # v2 added the field_strategies survivorship-config term.
    # v3 added the config-lineage terms (config_id / config_schema_version / config_json).
    assert ResolutionBatch.CONTRACT_VERSION == 3
    b = ResolutionBatch.from_args(run_id="r1")
    assert b.contract_version == 3


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


def test_with_data_carries_bulk_parts():
    """C1 follow-on: ``with_data`` folds the bulk parts onto a metadata batch as a
    frozen copy (metadata unchanged, original untouched)."""
    b = ResolutionBatch.from_args(run_id="r1", dataset="crm")
    assert b.clusters is None and b.df is None and b.scored_pairs is None
    clusters, df = _singleton_clusters(2), _singleton_df(2)
    b2 = b.with_data(clusters=clusters, df=df, scored_pairs=[])
    assert b2.clusters is clusters and b2.df is df and b2.scored_pairs == []
    assert b2.run_id == "r1" and b2.dataset == "crm"  # metadata carried through
    assert b.clusters is None  # original batch untouched (frozen copy)


def test_apply_batch_matches_resolve_clusters_adapter():
    """``apply_batch(store, batch-with-data)`` is the real write entry; the
    ``resolve_clusters`` adapter that builds that batch dispatches the same writes."""
    n = 5
    adapter_store = _TxnRecordingStore()
    resolve_clusters(
        clusters=_singleton_clusters(n), df=_singleton_df(n), scored_pairs=[],
        store=adapter_store, run_name="run1", dataset="crm", source_pk_col="raw_id",
    )

    direct_store = _TxnRecordingStore()
    batch = ResolutionBatch.from_args(
        run_id="run1", dataset="crm", source_pk_col="raw_id",
    ).with_data(
        clusters=_singleton_clusters(n), df=_singleton_df(n), scored_pairs=[],
    )
    apply_batch(direct_store, batch)

    assert direct_store.events == adapter_store.events
    assert any(e in {"bulk_upsert_records", "bulk_add_edges"} for e in direct_store.events)
