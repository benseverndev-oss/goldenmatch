"""Distributed identity resolution dispatch.

Polymorphic entry that accepts either an in-memory ``dict[int, dict]``
(today's resolver shape) or a Ray ``Dataset`` of cluster rows. For the
Ray path we materialize cluster aggregates on the driver via
``materialize_cluster_dict`` and run the existing ``resolve_clusters``
against a Postgres-backed ``IdentityStore``.

Per-partition ``map_batches`` is a follow-up: the in-process resolver
needs the full source-record payloads to upsert ``source_records`` and
build golden records, so the partition shape would also need the row
payloads. The materialize-then-resolve path is correct and unblocks
the Phase 6 wiring while we lift the resolver to be partition-friendly.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import polars as pl

    from goldenmatch.identity.resolve import ResolveSummary
    from goldenmatch.identity.store import IdentityStore

log = logging.getLogger("goldenmatch.distributed.identity")


def resolve_identities_distributed(
    clusters: Any,
    df: pl.DataFrame,
    scored_pairs: list[tuple[int, int, float]],
    matchkey_name: str | None,
    *,
    dsn: str,
    run_name: str,
    dataset: str | None = None,
    source_pk_col: str | None = None,
    pool_min_size: int = 2,
    pool_max_size: int = 8,
) -> ResolveSummary:
    """Resolve identities from clusters that may be a dict or Ray Dataset.

    Always runs against a Postgres backend (SQLite is single-process and
    Phase 6's whole point is to lift the single-process constraint).

    Returns a ``ResolveSummary`` (same shape as the in-memory resolver).
    """
    from goldenmatch.distributed._utils import is_ray_dataset
    from goldenmatch.identity.pool import get_identity_pool
    from goldenmatch.identity.resolve import resolve_clusters
    from goldenmatch.identity.store import IdentityStore

    if is_ray_dataset(clusters):
        # Materialize cluster aggregates back to dict shape so the existing
        # in-memory resolver can consume them. Phase 6 lift goal is to skip
        # this step once the resolver is partition-friendly.
        from goldenmatch.distributed.clustering import (
            materialize_cluster_dict,
            pairs_list_to_dataset,
        )

        log.info(
            "distributed identity: materializing Ray cluster dataset (driver-side)"
        )
        pairs_ds = pairs_list_to_dataset(scored_pairs)
        clusters_dict = materialize_cluster_dict(clusters, pairs_ds)
    else:
        clusters_dict = clusters

    pool = get_identity_pool(
        dsn, min_size=pool_min_size, max_size=pool_max_size
    )
    with pool.connection() as conn:
        store: IdentityStore = IdentityStore.__new__(IdentityStore)
        store._backend = "postgres"
        store._conn = conn
        store._pool = None  # already holding a checked-out conn
        return resolve_clusters(
            clusters_dict,
            df,
            scored_pairs,
            matchkey_name,
            store,
            run_name,
            dataset=dataset,
            source_pk_col=source_pk_col,
        )


def materialize_identity_assignments(summary: ResolveSummary) -> dict[str, int]:
    """Adapter that returns the count breakdown from a ResolveSummary."""
    return summary.as_dict()
