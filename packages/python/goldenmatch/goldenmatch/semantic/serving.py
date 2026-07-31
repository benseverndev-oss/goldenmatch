"""Semantic-layer ↔ Customer 360 drill-through.

A `ResolvedCrosswalk` (wedge B) maps each source record to the durable
control-plane ``resolved_entity_id`` — the exact same ``entity_id`` the Identity
Control Plane's Customer 360 view keys on. So a metric grouped on
``resolved_entity_id`` can drill straight through to the whole picture of that
customer (golden record + per-field provenance + linked source records + event
timeline + relationship neighborhood) with zero key translation.

`entity_360(store_path, entity_id)` is the direct read; `profile_from_crosswalk`
is the drill-through: a source primary key → its resolved entity → the 360 page.
"""
from __future__ import annotations

from typing import Any


def entity_360(
    store_path: str,
    entity_id: str,
    *,
    include_relationships: bool = True,
    timeline_limit: int | None = None,
) -> dict[str, Any] | None:
    """Open the identity store at `store_path` and return the Customer 360 page
    for `entity_id` (or None if the entity does not exist).

    Thin bridge over `goldenmatch.identity.customer_360_page` so a semantic-layer
    caller can serve the 360 for a resolved key without importing the identity
    subsystem directly.
    """
    from goldenmatch.identity import IdentityStore, customer_360_page

    with IdentityStore(path=store_path) as store:
        return customer_360_page(
            store,
            entity_id,
            include_relationships=include_relationships,
            timeline_limit=timeline_limit,
        )


def profile_from_crosswalk(
    crosswalk: Any,
    source_pk: Any,
    *,
    store_path: str | None = None,
    include_relationships: bool = True,
    timeline_limit: int | None = None,
) -> dict[str, Any] | None:
    """Drill through from a resolved crosswalk row to the Customer 360 view.

    Looks up `source_pk` in the `ResolvedCrosswalk`, reads its durable
    `resolved_entity_id`, and returns the full Customer 360 page for that entity
    from the same identity store — "click a metric row, see the customer".

    Args:
        crosswalk: a `ResolvedCrosswalk` (from `build_resolved_crosswalk`).
        source_pk: the source primary key value of the record to drill into.
        store_path: the identity store path. Defaults to the crosswalk's own
            `store_path` (set for a durable "resolve once" run); required if the
            crosswalk was ephemeral (its store was discarded, so there is nothing
            to drill into).
        include_relationships / timeline_limit: passed through to the 360 read.

    Returns:
        The Customer 360 page dict, or None if the `source_pk` isn't in the
        crosswalk, wasn't resolved to an entity, or the entity no longer exists.
    """
    path = store_path if store_path is not None else getattr(crosswalk, "store_path", None)
    if not path:
        raise ValueError(
            "profile_from_crosswalk: no identity store to drill into — pass "
            "store_path, or build the crosswalk with a durable store_path "
            "(an ephemeral crosswalk's store is discarded after resolution)."
        )

    table = crosswalk.table
    key = str(source_pk)
    pks = table.column("source_pk").to_pylist()
    resolved = table.column(crosswalk.resolved_key).to_pylist()
    entity_id: str | None = None
    for pk, eid in zip(pks, resolved):
        if pk == key:
            entity_id = eid
            break
    if not entity_id:
        return None  # unknown source_pk, or resolved to no entity (unmapped)

    return entity_360(
        path,
        entity_id,
        include_relationships=include_relationships,
        timeline_limit=timeline_limit,
    )
