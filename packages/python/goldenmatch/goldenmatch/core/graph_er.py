"""Multi-table / graph entity resolution.

Matches within entity types, then propagates evidence across relationships
to boost scores between related entities. Iterates until convergence.

Usage in config:
    entities:
      - name: customers
        sources: [{path: crm.csv, source_name: crm}]
        matchkeys: [{name: cust_fuzzy, type: weighted, ...}]

      - name: orders
        sources: [{path: orders.csv, source_name: orders}]
        matchkeys: [{name: order_exact, type: exact, ...}]

    relationships:
      - from: orders
        to: customers
        join_key: customer_id
        evidence_weight: 0.3

    graph:
      max_iterations: 5
      convergence_threshold: 0.01
      propagation_mode: additive
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import polars as pl

from goldenmatch.config.schemas import GoldenMatchConfig
from goldenmatch.core.cluster import build_clusters
from goldenmatch.core.pipeline import run_dedupe

logger = logging.getLogger(__name__)


@dataclass
class EntityType:
    """An entity type in the graph."""

    name: str
    sources: list[tuple[str, str]]  # (path, source_name)
    config: GoldenMatchConfig
    df: pl.DataFrame | None = None
    clusters: dict[int, dict] = field(default_factory=dict)
    scored_pairs: list[tuple[int, int, float]] = field(default_factory=list)


@dataclass
class Relationship:
    """A relationship between two entity types."""

    from_entity: str
    to_entity: str
    join_key: str  # foreign key in from_entity
    evidence_weight: float = 0.3


@dataclass
class GraphERResult:
    """Result of graph entity resolution."""

    entities: dict[str, EntityType]
    iterations: int
    converged: bool
    evidence_propagated: int  # number of pair score boosts applied


def run_graph_er(
    entities: list[EntityType],
    relationships: list[Relationship],
    max_iterations: int = 5,
    convergence_threshold: float = 0.01,
    propagation_mode: str = "additive",
    *,
    alpha: float = 0.5,
    rel_threshold: float = 0.5,
    rel_mode: str = "jaccard",
) -> GraphERResult:
    """Run multi-table entity resolution with evidence propagation.

    Algorithm (additive / multiplicative):
    1. Match within each entity type independently
    2. For each relationship, find linked records across entity types
    3. If linked records in entity A are matched, boost scores in entity B
    4. Re-cluster entity B with boosted scores
    5. Repeat until no scores change more than convergence_threshold

    propagation_mode="relational" replaces the flat-boost loop (steps 2-5) with
    collective resolution: it blends attribute similarity with neighbor-cluster
    overlap (relational similarity) and iterates to a fixpoint. The flat boost
    over-merges (it boosts ALL co-author pairs regardless of identity); collective
    resolution only merges records whose *neighborhoods* agree, which lifts F1
    well above the independent (attribute-only) baseline.

    Args:
        entities: List of EntityType configs.
        relationships: Cross-entity relationships.
        max_iterations: Max propagation iterations (also the collective fixpoint cap).
        convergence_threshold: Stop when max score change < this (flat-boost only).
        propagation_mode: "additive" (add weight), "multiplicative" (multiply),
            or "relational" (collective neighborhood-aware resolution).
        alpha: Relational vs attribute blend weight for the collective path
            (``blended = (1 - alpha) * attr + alpha * rel``). Ignored for flat boost.
        rel_threshold: Keep a blended pair as an edge iff ``blended >= rel_threshold``
            (collective path only).
        rel_mode: Relational-similarity mode for the collective path
            ("jaccard" / "adamic_adar").

    Returns:
        GraphERResult with final entity clusters and stats.
    """
    entity_map = {e.name: e for e in entities}

    # Step 1: Initial matching within each entity type
    for entity in entities:
        logger.info("Graph ER: matching entity '%s'", entity.name)
        result = run_dedupe(entity.sources, entity.config)
        entity.clusters = result.get("clusters", {})
        entity.scored_pairs = result.get("scored_pairs", [])

        # Load data for relationship lookups
        from goldenmatch.core.ingest import load_file
        frames = []
        for path, source_name in entity.sources:
            lf = load_file(path)
            lf = lf.with_columns(pl.lit(source_name).alias("__source__"))
            frames.append(lf.collect())
        entity.df = pl.concat(frames) if frames else pl.DataFrame()
        if "__row_id__" not in entity.df.columns:
            entity.df = entity.df.with_row_index("__row_id__")

    # Relational (collective) path: neighborhood-aware resolution instead of the
    # flat boost. Leaves additive/multiplicative untouched (the loop below).
    if propagation_mode == "relational":
        n_resolved = _run_collective(
            entity_map, relationships,
            alpha=alpha, rel_threshold=rel_threshold,
            rel_mode=rel_mode, max_iterations=max_iterations,
        )
        return GraphERResult(
            entities=entity_map,
            iterations=max_iterations,
            converged=True,
            evidence_propagated=n_resolved,
        )

    # Step 2-5: Iterative evidence propagation
    converged = False
    total_propagated = 0

    for iteration in range(max_iterations):
        max_delta = 0.0
        iteration_propagated = 0

        for rel in relationships:
            from_entity = entity_map.get(rel.from_entity)
            to_entity = entity_map.get(rel.to_entity)

            if from_entity is None or to_entity is None:
                logger.warning("Relationship references unknown entity: %s -> %s", rel.from_entity, rel.to_entity)
                continue

            if from_entity.df is None or to_entity.df is None:
                continue

            # Find evidence: which "to" records should have boosted scores
            # based on matched "from" records sharing the same join_key
            delta, n_boosted = _propagate_evidence(
                from_entity, to_entity, rel,
                propagation_mode=propagation_mode,
            )
            max_delta = max(max_delta, delta)
            iteration_propagated += n_boosted

        total_propagated += iteration_propagated

        logger.info(
            "Graph ER iteration %d: max_delta=%.4f, boosted=%d pairs",
            iteration + 1, max_delta, iteration_propagated,
        )

        if max_delta < convergence_threshold or iteration_propagated == 0:
            converged = True
            break

        # Re-cluster entities that received evidence
        for entity in entities:
            if entity.scored_pairs:
                all_ids = list(range(entity.df.height)) if entity.df is not None else []
                if entity.df is not None and "__row_id__" in entity.df.columns:
                    all_ids = entity.df["__row_id__"].to_list()
                max_cluster = 100
                if entity.config.golden_rules:
                    max_cluster = entity.config.golden_rules.max_cluster_size
                entity.clusters = build_clusters(entity.scored_pairs, all_ids, max_cluster)

    return GraphERResult(
        entities=entity_map,
        iterations=iteration + 1 if not converged else iteration + 1,
        converged=converged,
        evidence_propagated=total_propagated,
    )


def _build_fk_lookup(
    from_entity: EntityType,
    to_entity: EntityType,
    rel: Relationship,
) -> tuple[dict, dict] | None:
    """Resolve a relationship's FK into reusable lookup maps.

    Returns ``(from_id_to_key, key_to_to_ids)`` where:
      * ``from_id_to_key``: ``{from_row_id -> join_key_value}``
      * ``key_to_to_ids``:  ``{join_key_value -> [to_row_id, ...]}``

    The join key lives on ``from_entity`` (``rel.join_key``); it is matched
    against the same-named column on ``to_entity`` (falling back to ``id``).
    Returns ``None`` if the relationship can't be resolved (missing frames or
    columns), mirroring the early-outs in :func:`_propagate_evidence`.
    """
    if from_entity.df is None or to_entity.df is None:
        return None

    join_key = rel.join_key
    if join_key not in from_entity.df.columns:
        logger.warning("Join key '%s' not found in entity '%s'", join_key, from_entity.name)
        return None

    # from_row_id -> join_key_value
    from_rows = from_entity.df.select(["__row_id__", join_key]).to_dicts()
    from_id_to_key = {r["__row_id__"]: r[join_key] for r in from_rows}

    # join_key_value -> to_row_ids (direct key, else via to_entity's `id` column)
    if join_key in to_entity.df.columns:
        to_rows = to_entity.df.select(["__row_id__", join_key]).to_dicts()
        to_key = join_key
    elif "id" in to_entity.df.columns:
        to_rows = to_entity.df.select(["__row_id__", "id"]).to_dicts()
        to_key = "id"
    else:
        return None

    key_to_to_ids: dict = {}
    for r in to_rows:
        val = r[to_key]
        if val is not None:
            key_to_to_ids.setdefault(val, []).append(r["__row_id__"])

    return from_id_to_key, key_to_to_ids


def _propagate_evidence(
    from_entity: EntityType,
    to_entity: EntityType,
    rel: Relationship,
    propagation_mode: str = "additive",
) -> tuple[float, int]:
    """Propagate match evidence from one entity to another.

    If records A1 and A2 in from_entity are in the same cluster,
    and A1.join_key = B1 and A2.join_key = B2 in to_entity,
    then boost the score between B1 and B2.

    Returns (max_score_delta, n_boosted_pairs).
    """
    lookup = _build_fk_lookup(from_entity, to_entity, rel)
    if lookup is None:
        return 0.0, 0
    from_id_to_key, key_to_to_ids = lookup

    # For each cluster in from_entity, find linked to_entity pairs
    max_delta = 0.0
    n_boosted = 0
    existing_scores = {(min(a, b), max(a, b)): s for a, b, s in to_entity.scored_pairs}

    for cid, cinfo in from_entity.clusters.items():
        if cinfo["size"] < 2:
            continue

        # Find to_entity records linked to this cluster's members
        linked_to_ids = set()
        for member_id in cinfo["members"]:
            fk_value = from_id_to_key.get(member_id)
            if fk_value is not None:
                for to_id in key_to_to_ids.get(fk_value, []):
                    linked_to_ids.add(to_id)

        if len(linked_to_ids) < 2:
            continue

        # Boost scores between all linked to_entity records
        linked_list = sorted(linked_to_ids)
        for i in range(len(linked_list)):
            for j in range(i + 1, len(linked_list)):
                pair_key = (linked_list[i], linked_list[j])
                old_score = existing_scores.get(pair_key, 0.0)

                if propagation_mode == "multiplicative":
                    new_score = min(1.0, old_score * (1 + rel.evidence_weight))
                else:  # additive
                    new_score = min(1.0, old_score + rel.evidence_weight)

                delta = abs(new_score - old_score)
                if delta > 0:
                    max_delta = max(max_delta, delta)
                    existing_scores[pair_key] = new_score
                    n_boosted += 1

    # Update to_entity scored_pairs
    to_entity.scored_pairs = [(a, b, s) for (a, b), s in existing_scores.items()]

    return max_delta, n_boosted


# ---------------------------------------------------------------------------
# Collective (relational) path
# ---------------------------------------------------------------------------

def _invert_clusters(clusters: dict) -> dict:
    """``{cid -> {"members": [...], ...}}`` -> ``{rid -> cid}``."""
    rid_to_cid: dict = {}
    for cid, cinfo in clusters.items():
        for rid in cinfo["members"]:
            rid_to_cid[rid] = cid
    return rid_to_cid


def _clusters_from_rid_to_cid(rid_to_cid: dict) -> dict:
    """``{rid -> cid}`` -> ``{cid -> {"members": [...], "size": ...}}``.

    Inverse of :func:`_invert_clusters`. Members are sorted for determinism;
    ``members`` / ``size`` match the field names :func:`_propagate_evidence`
    (and the rest of the pipeline) read off the cluster dict.
    """
    members_by_cid: dict = {}
    for rid, cid in rid_to_cid.items():
        members_by_cid.setdefault(cid, []).append(rid)
    return {
        cid: {"members": sorted(members), "size": len(members)}
        for cid, members in members_by_cid.items()
    }


def _cooccurrence_groups(
    from_entity: EntityType,
    to_entity: EntityType,
    rel: Relationship,
) -> list[list[tuple[str, int]]]:
    """Co-occurrence groups of ``to_entity`` records for collective resolution.

    Two ``to_entity`` records co-occur when they link to the same ``from_entity``
    cluster (e.g. two authors who share a paper). Reuses the SAME FK resolution
    as :func:`_propagate_evidence` (:func:`_build_fk_lookup`): for each
    ``from_entity`` cluster, gather the ``to_entity`` rids linked via the join
    key -> one group of ``(to_entity.name, rid)`` tuples per cluster.
    """
    lookup = _build_fk_lookup(from_entity, to_entity, rel)
    if lookup is None:
        return []
    from_id_to_key, key_to_to_ids = lookup

    groups: list[list[tuple[str, int]]] = []
    for cinfo in from_entity.clusters.values():
        linked: set[int] = set()
        for member_id in cinfo["members"]:
            fk_value = from_id_to_key.get(member_id)
            if fk_value is not None:
                linked.update(key_to_to_ids.get(fk_value, ()))
        if len(linked) >= 2:
            groups.append([(to_entity.name, rid) for rid in sorted(linked)])
    return groups


def _run_collective(
    entity_map: dict,
    relationships: list[Relationship],
    *,
    alpha: float,
    rel_threshold: float,
    rel_mode: str,
    max_iterations: int,
) -> int:
    """Collective resolution for the resolved (``to``) entity of each relationship.

    Builds co-occurrence groups from the relationships, runs
    :func:`collective_resolve`, and writes the result back onto the resolved
    entity's ``.clusters`` in the standard ``{cid -> {members, size}}`` shape.

    Returns the number of resolved records (sum across resolved entities) as a
    cheap stat for ``GraphERResult.evidence_propagated``.
    """
    from goldenmatch.core.collective import build_neighbor_index, collective_resolve

    # Group co-occurrence edges by the entity being resolved (the rel `to`).
    groups_by_resolved: dict[str, list] = {}
    for rel in relationships:
        from_entity = entity_map.get(rel.from_entity)
        to_entity = entity_map.get(rel.to_entity)
        if from_entity is None or to_entity is None:
            logger.warning(
                "Relationship references unknown entity: %s -> %s",
                rel.from_entity, rel.to_entity,
            )
            continue
        if from_entity.df is None or to_entity.df is None:
            continue
        groups_by_resolved.setdefault(rel.to_entity, []).extend(
            _cooccurrence_groups(from_entity, to_entity, rel)
        )

    n_resolved = 0
    for resolved_name, groups in groups_by_resolved.items():
        resolved = entity_map[resolved_name]
        neighbor_index = build_neighbor_index(groups)

        all_ids = (
            resolved.df["__row_id__"].to_list()
            if resolved.df is not None and "__row_id__" in resolved.df.columns
            else []
        )
        entity_state = {
            resolved_name: {
                "attr_pairs": resolved.scored_pairs,
                "ids": all_ids,
                "clusters": _invert_clusters(resolved.clusters),
            }
        }

        max_cluster = 100
        if resolved.config.golden_rules:
            max_cluster = resolved.config.golden_rules.max_cluster_size

        result = collective_resolve(
            entity_state, neighbor_index,
            alpha=alpha, rel_mode=rel_mode, threshold=rel_threshold,
            max_iterations=max_iterations, max_cluster_size=max_cluster,
        )
        resolved.clusters = _clusters_from_rid_to_cid(result[resolved_name])
        n_resolved += len(result[resolved_name])

    return n_resolved
