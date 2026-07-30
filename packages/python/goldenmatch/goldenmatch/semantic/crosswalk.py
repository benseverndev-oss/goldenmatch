"""Resolve once, emit a durable entity crosswalk for the semantic layer (wedge B).

A semantic layer joins on entity-key equality but never resolves those keys.
Wedge A *certifies* a declared key; wedge B *produces the conformed key*: run
GoldenMatch entity resolution, then hand back a `{source, source_pk,
resolved_entity_id}` crosswalk keyed on the control plane's **durable** stable
`entity_id` (UUIDv7). Point the semantic layer's joins at `resolved_entity_id`
and every metric inherits correct, conformed joins — "resolve once."

The resolved key is the Identity Control Plane's stable id (owned there — this is
not a second identity scheme). The builder runs the normal `dedupe_df` pipeline
with the identity graph enabled and reads the assigned ids back from
`source_records` by record id. Pass a `store_path` to make the ids durable across
runs (the whole point of "resolve once"); omit it for an ephemeral run.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Any

import pyarrow as pa

from goldenmatch.semantic.key_integrity import _to_arrow


@dataclass
class ResolvedCrosswalk:
    """A `{source, source_pk, resolved_entity_id}` crosswalk over resolved identity.

    `table` has one row per input record, in input order. `resolved_entity_id` is
    the durable control-plane entity id; rows entity resolution could not place
    (rare — e.g. a null source_pk) carry a null id and are counted in `unmapped`.
    """

    table: pa.Table
    source: str
    source_pk_column: str
    resolved_key: str = "resolved_entity_id"
    n_records: int = 0
    n_entities: int = 0
    unmapped: int = 0
    store_path: str | None = None
    note: str = ""

    def to_arrow(self) -> pa.Table:
        return self.table

    @property
    def reduction_ratio(self) -> float:
        """1 - entities/records: how much the resolved key collapses the source
        key space (0 = every source_pk is already its own entity)."""
        if not self.n_records:
            return 0.0
        return 1.0 - (self.n_entities / self.n_records)


def build_resolved_crosswalk(
    df: Any,
    *,
    source_pk: str,
    source_name: str = "dataframe",
    dataset: str | None = None,
    store_path: str | None = None,
    config: Any = None,
) -> ResolvedCrosswalk:
    """Resolve `df` and return a durable `{source, source_pk, resolved_entity_id}` crosswalk.

    Args:
        df: the source table (pyarrow / polars / pandas / dict).
        source_pk: the column holding each record's source primary key.
        source_name: logical source name (the `{source}` half of the record id).
        dataset: identity-graph dataset scope (defaults to `source_name`).
        store_path: SQLite path for the identity graph. Pass a stable path to make
            entity ids durable across runs ("resolve once"); omit for an ephemeral
            temp store.
        config: an explicit GoldenMatchConfig; zero-config auto-config otherwise.

    Returns:
        A `ResolvedCrosswalk`. The resolved key is the control plane's stable id.
    """
    from goldenmatch._api import dedupe_df
    from goldenmatch.config.schemas import IdentityConfig
    from goldenmatch.core.autoconfig import auto_configure_df
    from goldenmatch.identity.store import IdentityStore

    table = _to_arrow(df)
    if source_pk not in set(table.column_names):
        raise ValueError(f"build_resolved_crosswalk: source_pk column not in table: {source_pk!r}")

    dataset = dataset or source_name
    ephemeral = store_path is None
    if ephemeral:
        fd, store_path = tempfile.mkstemp(prefix="gm_crosswalk_", suffix=".db")
        os.close(fd)

    # Zero-config (or the caller's config), with the identity graph enabled so the
    # pipeline assigns durable stable ids. Disable rerank so certification never
    # blocks on a cross-encoder download (offline-safe, as in wedge A).
    cfg = config if config is not None else auto_configure_df(df, confidence_required=False)
    for mk in cfg.get_matchkeys():
        if getattr(mk, "rerank", False):
            mk.rerank = False
    cfg.identity = IdentityConfig(
        enabled=True,
        backend="sqlite",
        path=store_path,
        source_pk_column=source_pk,
        dataset=dataset,
    )

    dedupe_df(df, config=cfg, source_name=source_name, confidence_required=False)

    store = IdentityStore(backend="sqlite", path=store_path)
    pks = table.column(source_pk).to_pylist()
    record_ids = [f"{source_name}:{pk}" for pk in pks]
    mapping = store.lookup_entity_ids(record_ids)
    resolved = [mapping.get(rid) for rid in record_ids]

    out = pa.table({
        "source": [source_name] * len(pks),
        "source_pk": [None if pk is None else str(pk) for pk in pks],
        "resolved_entity_id": resolved,
    })
    n_entities = len({r for r in resolved if r is not None})
    unmapped = sum(1 for r in resolved if r is None)
    note = ""
    if unmapped:
        note = f"{unmapped} record(s) unresolved (null/duplicate source_pk?)"
    if ephemeral:
        note = (note + "; " if note else "") + "ephemeral store — pass store_path for durable ids across runs"

    return ResolvedCrosswalk(
        table=out,
        source=source_name,
        source_pk_column=source_pk,
        n_records=len(pks),
        n_entities=n_entities,
        unmapped=unmapped,
        store_path=None if ephemeral else store_path,
        note=note,
    )
