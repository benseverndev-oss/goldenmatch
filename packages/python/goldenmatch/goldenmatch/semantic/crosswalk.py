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

import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Any

import pyarrow as pa

from goldenmatch.semantic.key_integrity import _to_arrow

logger = logging.getLogger(__name__)


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
            temp store. Ignored (with a warning) when `config.identity.backend` is
            not sqlite, since a postgres store is addressed by its connection.
        config: an explicit GoldenMatchConfig; zero-config auto-config otherwise.
            Its `identity` section is RESPECTED, not replaced: `backend`,
            `connection` and `emit_singletons` are the caller's to set. This
            function overrides only what it is inherently deciding -- `enabled`,
            `source_pk_column`, `dataset`, and the sqlite `path` when a
            `store_path` was given. `emit_singletons=False` and
            `backend="postgres"` are the two documented single-node scale levers
            (see `docs-site/goldenmatch/identity-graph.mdx`); both reach the
            resolver through here.

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

    # Zero-config (or the caller's config), with the identity graph enabled so the
    # pipeline assigns durable stable ids. Disable rerank so certification never
    # blocks on a cross-encoder download (offline-safe, as in wedge A).
    cfg = config if config is not None else auto_configure_df(df, confidence_required=False)
    for mk in cfg.get_matchkeys():
        if getattr(mk, "rerank", False):
            mk.rerank = False

    # MERGE the caller's identity settings rather than replacing them (#2521).
    # This function owns only what it is inherently deciding -- it is resolving
    # THIS frame into THIS dataset -- and must leave the storage and cost levers
    # (`backend`, `connection`, `emit_singletons`) to the caller. Those are the
    # only two single-node scale controls the identity docs describe, and
    # hardcoding them here made the documented guidance unreachable through this
    # entry point, silently.
    #
    # Backward compatible by construction: `IdentityConfig`'s defaults are
    # `backend="sqlite"` and `emit_singletons=True`, which is exactly the
    # behaviour this used to hardcode. A config whose `identity` was never
    # touched resolves to the same run as before.
    supplied = getattr(cfg, "identity", None)
    identity = IdentityConfig() if supplied is None else supplied.model_copy(deep=True)
    identity.enabled = True
    identity.source_pk_column = source_pk
    identity.dataset = dataset

    # The ephemeral temp store is a SQLite-only concept; a postgres store is
    # durable by definition and has no file to create or clean up.
    ephemeral = identity.backend == "sqlite" and store_path is None
    if identity.backend == "sqlite":
        if store_path is None:
            fd, store_path = tempfile.mkstemp(prefix="gm_crosswalk_", suffix=".db")
            os.close(fd)
        identity.path = store_path
    elif store_path is not None:
        # Don't drop it silently -- that is the failure mode this fixes.
        logger.warning(
            "build_resolved_crosswalk: store_path=%r is ignored because "
            "config.identity.backend is %r, not 'sqlite'. The identity graph will "
            "use the configured connection; pass backend='sqlite' if you meant to "
            "write to that file.",
            store_path, identity.backend,
        )
    cfg.identity = identity

    try:
        dedupe_df(df, config=cfg, source_name=source_name, confidence_required=False)

        store = IdentityStore(
            backend=identity.backend,
            path=identity.path,
            connection=identity.connection,
        )
        pks = table.column(source_pk).to_pylist()
        record_ids = [f"{source_name}:{pk}" for pk in pks]
        mapping = store.lookup_entity_ids(record_ids)
        resolved = [mapping.get(rid) for rid in record_ids]
    finally:
        if ephemeral:
            # The ephemeral store is a throwaway temp DB — remove it (and any
            # SQLite -wal/-shm sidecars) so repeated ephemeral runs don't leak files.
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(store_path + suffix)
                except OSError:
                    # Best-effort cleanup: the sidecar may not exist, and a
                    # unlink failure must not mask an otherwise-successful result.
                    pass

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
        # Only report a path the run actually used: it is None for an ephemeral
        # store, and for a non-sqlite backend where any `store_path` was ignored.
        store_path=(
            store_path if (identity.backend == "sqlite" and not ephemeral) else None
        ),
        note=note,
    )
