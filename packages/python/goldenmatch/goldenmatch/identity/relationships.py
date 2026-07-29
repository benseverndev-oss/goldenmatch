"""Semantic-graph: entity<->entity relationship edges (#semantic-graph).

Identity resolution collapses records that are the SAME entity. The same blocking
data also surfaces DIFFERENT entities that share a NON-identity attribute -- two
prescribers on one clinic phone, two people at one address -- which is a
relationship, not a merge, and today it is discarded. ``build_relationships``
turns those shared attributes into edges between the durable ``entity_id``s,
turning the identity graph into a semantic graph maintained in the same pass.

Edges are stored in ``identity_relationships`` (entity-level, distinct from the
record-level ``evidence_edges``) and de-duped by primary key, so a re-resolve is
idempotent. Hub values (a switchboard line shared by hundreds of entities) are
skipped via ``RelationshipRule.max_fanout`` -- they are not pairwise relationships.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from goldenmatch.config.schemas import RelationshipRule
    from goldenmatch.identity.store import IdentityStore


def build_relationships(
    store: IdentityStore,
    rules: list[RelationshipRule] | None,
    dataset: str | None = None,
) -> tuple[int, int]:
    """Derive entity<->entity relationship edges from shared non-identity
    attributes and RECONCILE them into ``store`` so the graph equals the current
    desired state: new edges inserted, stale edges deleted, merges/splits reflected
    (``desired`` is recomputed from current entity ids). Idempotent -- same data
    twice writes/deletes nothing. Returns ``(inserted, deleted)``.

    Reconciliation is per ``(dataset, kind)``, so rules that share a ``kind`` union
    into one desired set, and a kind whose rule now yields NO edges (its shared
    values disappeared) still has its stale edges cleared (reconcile-to-empty)."""
    if not rules:
        return (0, 0)
    by_kind: dict[str, list[tuple]] = {}
    for rule in rules:
        groups = store.relationship_groups(
            rule.field, dataset, rule.min_entities, rule.max_fanout,
            rule.transform,
        )
        for value, eids in groups:
            uniq = sorted({e for e in eids if e})
            bucket = by_kind.setdefault(rule.kind, [])
            for i in range(len(uniq)):
                for j in range(i + 1, len(uniq)):
                    bucket.append(
                        (uniq[i], uniq[j], rule.kind, rule.field, value, dataset)
                    )
    # Kinds whose rules produced nothing this run must still reconcile to empty so
    # their prior-run edges get deleted, not left stale.
    for rule in rules:
        by_kind.setdefault(rule.kind, [])
    inserted = deleted = 0
    for kind, desired in by_kind.items():
        ins, dele, _ = store.reconcile_relationships(dataset, kind, desired)
        inserted += ins
        deleted += dele
    return (inserted, deleted)


# Field-name hints -> a derived transform worth trying alongside the raw value, so
# auto-detect can discover that (e.g.) `email_address` is a great edge field UNDER
# `email_domain` even though the raw address is unique.
_TRANSFORM_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("email",), "email_domain"),
    (("zip", "postal"), "zip3"),
    (("org", "company", "employer", "practice", "business", "clinic",
      "hospital", "facility", "institution", "group", "affiliation"),
     "normalize_company"),
)
# Never suggest edges on identity/plumbing columns (they relate a record to itself,
# not two entities) -- skipped by default in addition to any caller ``exclude``.
_DEFAULT_SKIP = frozenset({
    "unique_id", "id", "row_id", "record_id", "entity_id", "source", "dataset",
})


def _candidate_transforms(field: str) -> list[str | None]:
    f = field.lower()
    cands: list[str | None] = [None]
    for keys, transform in _TRANSFORM_HINTS:
        if any(k in f for k in keys):
            cands.append(transform)
    return cands


def profile_relationship_fields(
    store: IdentityStore,
    dataset: str | None = None,
    *,
    min_entities: int = 2,
    max_fanout: int = 50,
    exclude: list[str] | None = None,
    sample_keys: int = 500,
) -> list[dict]:
    """Rank every candidate ``(field, best transform)`` by how many entities it
    would actually give edges to (``coverage_entities``), with fanout measured on
    the FULL data (``store.relationship_field_stats``), not a sample. For each field
    the raw value and any name-hinted transform (email_domain / normalize_company /
    zip3) are profiled and the highest-coverage variant kept.

    This is the scale-correct core of auto-detect: hub attributes (specialty,
    degree, state -- a value held by ~everyone) fall out with ``coverage=0``, and a
    unique-looking field (email_address) surfaces under its transform (email_domain)
    rather than being dismissed on its raw value. ``sample_keys`` only bounds how
    many payloads are peeked to DISCOVER field names (keys are stable). Returns
    dicts ``{field, transform, coverage_entities, sweet_values, hub_values}``,
    best first (only fields that would produce at least one edge)."""
    from goldenmatch.identity.store import _SAFE_FIELD

    skip = _DEFAULT_SKIP | {c.lower() for c in (exclude or ())}
    fields: set[str] = set()
    for _eid, payload in store.sample_records(dataset, sample_keys):
        for key in payload:
            if key.lower() not in skip and _SAFE_FIELD.fullmatch(key):
                fields.add(key)

    ranked: list[dict] = []
    for field in sorted(fields):
        best: dict | None = None
        for transform in _candidate_transforms(field):
            st = store.relationship_field_stats(
                field, dataset, min_entities, max_fanout, transform)
            if st["coverage_entities"] <= 0:
                continue
            if best is None or st["coverage_entities"] > best["coverage_entities"]:
                best = {"field": field, "transform": transform, **st}
        if best is not None:
            ranked.append(best)
    ranked.sort(key=lambda r: r["coverage_entities"], reverse=True)
    return ranked


def suggest_relationship_rules(
    store: IdentityStore,
    dataset: str | None = None,
    *,
    min_entities: int = 2,
    max_fanout: int = 50,
    top_k: int = 8,
    exclude: list[str] | None = None,
    sample_keys: int = 500,
) -> list[RelationshipRule]:
    """SUGGEST relationship rules for the fields the program identifies as good edge
    sources -- the "program identifies" half of the feature (the other half is
    writing ``RelationshipRule``s by hand). Wraps ``profile_relationship_fields``:
    ranks candidate ``(field, transform)`` by true full-data entity coverage, so it
    prefers high-yield derived fields (email_domain) and rejects hub attributes
    (specialty/degree/state) instead of ranking them first (the flaw of the old
    LIMIT-sampled raw-value heuristic). Returns up to ``top_k`` rules, best first."""
    from goldenmatch.config.schemas import RelationshipRule

    ranked = profile_relationship_fields(
        store, dataset, min_entities=min_entities, max_fanout=max_fanout,
        exclude=exclude, sample_keys=sample_keys)
    rules = []
    for r in ranked[:top_k]:
        field, transform = r["field"], r["transform"]
        kind = f"shares_{field}" if transform is None else f"shares_{field}_{transform}"
        rules.append(RelationshipRule(
            field=field, kind=kind, transform=transform,
            min_entities=min_entities, max_fanout=max_fanout))
    return rules


def _entity_name(node: object, name_field: str | None) -> str | None:
    """A display name for an entity: the configured golden-record field, else the
    first non-empty string value of the golden record."""
    golden = getattr(node, "golden_record", None)
    if not isinstance(golden, dict):
        return None
    if name_field is not None:
        v = golden.get(name_field)
        return str(v) if v not in (None, "") else None
    for v in golden.values():
        if isinstance(v, str) and v.strip():
            return v
    return None


def to_graph_batch(
    store: IdentityStore,
    dataset: str | None = None,
    *,
    name_field: str | None = None,
    valid_from: int = 0,
) -> dict:
    """Export the identity relationship graph as a GoldenGraph ``StoreBatch`` dict
    -- the ``{entities, edges}`` JSON shape that ``goldengraph``'s ``PyStore.append``
    consumes -- so the durable identity graph feeds GoldenGraph's path-retrieval /
    answer (GraphRAG) layer.

    Each entity carries the stable ``entity_id`` in ``record_keys``, which is
    exactly how GoldenGraph's store reconciles the same node across appends
    (``store.rs::append``): re-exporting after a later resolve updates the same
    node instead of minting a new one. Edges become ``(subj_local, predicate=kind,
    obj_local)`` triples. Pure data -- no ``goldengraph`` import required. Append
    with ``pystore.append(json.dumps(to_graph_batch(store)))``.
    """
    rels = store.list_relationships(dataset)
    eids = sorted({e for r in rels for e in (r["entity_a_id"], r["entity_b_id"])})
    local = {eid: i for i, eid in enumerate(eids)}
    nodes = store.get_identities(set(eids)) if eids else {}
    refs = [dataset] if dataset else []

    entities = []
    for eid in eids:
        name = _entity_name(nodes.get(eid), name_field) or eid
        entities.append({
            "local_id": local[eid],
            "canonical_name": name,
            "typ": "entity",
            "surface_names": [name],
            "record_keys": [eid],          # stable cross-append reconciliation key
            "source_refs": list(refs),
        })

    edges = []
    for r in rels:
        edges.append({
            "subj_local": local[r["entity_a_id"]],
            "predicate": r["kind"],
            "obj_local": local[r["entity_b_id"]],
            "valid_from": valid_from,
            "valid_to": None,
            "source_refs": [r["shared_value"]] if r.get("shared_value") else list(refs),
        })

    return {"entities": entities, "edges": edges}
