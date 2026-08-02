"""Semantic-layer ↔ Customer 360 drill-through.

A `ResolvedCrosswalk` (wedge B) maps each source record to the durable
control-plane ``resolved_entity_id`` — the exact same ``entity_id`` the Identity
Control Plane's Customer 360 view keys on. So a metric grouped on
``resolved_entity_id`` can drill straight through to the whole picture of that
customer (golden record + per-field provenance + linked source records + event
timeline + relationship neighborhood) with zero key translation.

`entity_360(store_path, entity_id)` is the direct read; `profile_from_crosswalk`
is the drill-through: a source primary key → its resolved entity → the 360 page.

`certify_serving_joins(store)` closes the loop the other way: a Customer 360
serving view is itself a join surface (golden record ⋈ source records ⋈ events ⋈
relationships, all on the durable `entity_id` / `record_id`), so it carries the
same fan-out / double-count risk `certify_key_integrity` was built to catch. It
certifies that the serving layer's source-record join key is unique — so a metric
joined through the 360 provably can't double-count.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ServingJoinCertificate:
    """Certifies the join keys a Customer 360 serving layer rolls metrics up on.

    `record_certificate` is a `KeyIntegrityCertificate` over the source-record
    join key (`record_id` = `{source}:{source_pk}`): unique means a fact joined to
    source records and rolled up to the entity can't double-count. `n_entities` /
    `n_records` are the population it was computed over; `truncated` is True when a
    `max_entities` cap stopped the scan short (so the cert covers a prefix).
    """

    record_certificate: Any            # KeyIntegrityCertificate
    n_entities: int
    n_records: int
    truncated: bool = False

    @property
    def is_trustworthy(self) -> bool:
        return bool(self.record_certificate.is_trustworthy())


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


def certify_serving_joins(
    store: Any,
    *,
    dataset: str | None = None,
    status: str | None = "active",
    page_size: int = 500,
    max_entities: int | None = None,
) -> ServingJoinCertificate:
    """Certify that a Customer 360 serving layer's join keys don't double-count.

    A C360 view joins the golden record to its source records (and events /
    relationships) on the durable `entity_id`, and a dashboard typically joins a
    fact table to those source records on `record_id` (`{source}:{source_pk}`)
    before rolling up to the entity. If a `record_id` is duplicated, that roll-up
    silently double-counts. This walks the store's active entities, assembles the
    `record_id` join key across their source records, and runs
    `certify_key_integrity` over it — turning the serving layer's implicit
    join-key trust into an advisory `KeyIntegrityCertificate`.

    Args:
        store: an open `IdentityStore`.
        dataset: restrict to one identity-graph dataset (default: all).
        status: entity status to include (default `"active"`; None = all).
        page_size: pagination size for the entity scan.
        max_entities: cap the scan at this many entities (the cert then covers a
            prefix and sets `truncated=True`); None scans every entity.

    Returns:
        A `ServingJoinCertificate` whose `record_certificate.is_trustworthy()` is
        True when every source record has a unique join key.
    """
    from goldenmatch.semantic.key_integrity import certify_key_integrity

    record_ids: list[str] = []
    entity_ids: list[str] = []
    offset = 0
    truncated = False
    while True:
        limit = page_size
        if max_entities is not None:
            remaining = max_entities - len(entity_ids)
            if remaining <= 0:
                truncated = True
                break
            limit = min(page_size, remaining)
        nodes = store.list_identities(
            dataset=dataset, status=status, limit=limit, offset=offset
        )
        if not nodes:
            break
        for node in nodes:
            entity_ids.append(node.entity_id)
            for rec in store.get_records_for_entity(node.entity_id):
                record_ids.append(rec.record_id)
        offset += len(nodes)
        if len(nodes) < limit:
            break

    # certify_key_integrity needs at least one row; an empty store is trivially
    # trustworthy (no records means nothing can double-count).
    table = {"record_id": record_ids or [None]}
    cert = certify_key_integrity(table, key="record_id")
    return ServingJoinCertificate(
        record_certificate=cert,
        n_entities=len(entity_ids),
        n_records=len(record_ids),
        truncated=truncated,
    )
