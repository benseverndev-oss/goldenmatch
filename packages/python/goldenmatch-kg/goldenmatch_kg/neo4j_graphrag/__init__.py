"""neo4j-graphrag GoldenMatchResolver: drops goldenmatch in as the entity-resolution
stage of neo4j-graphrag's experimental pipeline.

IMPORTING THIS MODULE REQUIRES the `neo4j-graphrag` extra to be installed. The
base class (BasePropertySimilarityResolver) is imported at module top so that
GoldenMatchResolver can be a real subclass at class-definition time. This is
intentional and standard: users import `goldenmatch_kg.neo4j_graphrag` ONLY after
`pip install goldenmatch-kg[neo4j-graphrag]`.

Override point (confirmed against neo4j-graphrag installed in the local venv):
    BasePropertySimilarityResolver.run(self) -> ResolutionStats

    Confirmed signature (from inspect.getsource on the installed version):
        async def run(self) -> ResolutionStats:
            ...

    The base run() uses self.driver to:
      1. Query Neo4j for candidate __Entity__ nodes grouped by label.
      2. Compute pairwise compute_similarity() + _consolidate_sets() to find merge sets.
      3. Call apoc.refactor.mergeNodes on each merge set via self.driver.

    GoldenMatchResolver replaces step 2 (pairwise similarity + consolidation) with
    goldenmatch's zero-config dedupe_df pipeline, while keeping step 3 (APOC merge)
    from the base. For tests, resolve_entities_for_test() exposes the clustering
    decision in-process without touching Neo4j.

    Also required: implement the abstract method compute_similarity(). We provide a
    no-op stub (returns 0.0 always) since goldenmatch replaces that decision path
    entirely and the stub is never called on the hot path.

Usage::

    from unittest.mock import MagicMock
    from goldenmatch_kg.neo4j_graphrag import GoldenMatchResolver

    resolver = GoldenMatchResolver(driver=MagicMock())
    groups = resolver.resolve_entities_for_test([
        ("1", "Apple Inc", "org"),
        ("2", "Apple",     "org"),
        ("3", "Microsoft", "org"),
    ])
    # -> [["1", "2"], ["3"]]  (Apple Inc + Apple merged; Microsoft singleton)
"""
from __future__ import annotations

from neo4j_graphrag.experimental.components.resolver import (  # pyright: ignore[reportMissingImports]
    BasePropertySimilarityResolver,
    ResolutionStats,
)

from goldenmatch_kg.neo4j_graphrag._resolve import resolve_records


class GoldenMatchResolver(BasePropertySimilarityResolver):
    """Replaces neo4j-graphrag's pairwise fuzzy resolution with goldenmatch zero-config.

    The library's BasePropertySimilarityResolver.run() groups candidate nodes by
    entity label, computes pairwise similarity via compute_similarity(), consolidates
    pairs into merge sets via _consolidate_sets(), then writes them to Neo4j via
    APOC. This subclass overrides run() to replace the pairwise-similarity step with
    a call to goldenmatch's zero-config dedupe_df pipeline, which uses auto-configured
    blocking + fuzzy scoring + clustering and is generally higher-recall than
    fixed-threshold WRatio scoring.

    The driver arg is passed through to the base class and is used ONLY for the
    Neo4j APOC merge calls (step 3). It is NOT used for the clustering decision
    (steps 1-2), so it can be MagicMock()'d for tests.
    """

    def compute_similarity(self, text_a: str, text_b: str) -> float:
        """Stub: goldenmatch replaces the pairwise similarity step entirely.

        This method is abstract on the base class and must be implemented, but
        GoldenMatchResolver's run() calls goldenmatch (via resolve_records) for
        the clustering decision and never calls compute_similarity().
        """
        return 0.0  # never called on the goldenmatch path

    async def run(self) -> ResolutionStats:  # type: ignore[override]
        """Override: replace pairwise similarity with goldenmatch clustering.

        Replicates the base run() structure (same Cypher to fetch entities, same
        APOC merge Cypher, same ResolutionStats return) but replaces step 2
        (compute_similarity + _consolidate_sets) with goldenmatch's zero-config
        pipeline (resolve_records -> core.resolve_entities).
        """
        # Step 1: Query Neo4j for candidate entities (same as base).
        match_query = "MATCH (entity:__Entity__)"
        if self.filter_query:
            match_query += f" {self.filter_query}"

        # Build a flat list of properties to pull (default: ["name"]).
        props_map_list = [
            f"{prop}: entity.{prop}" for prop in self.resolve_properties
        ]
        props_map = ", ".join(props_map_list)

        query = f"""
            {match_query}
            UNWIND labels(entity) AS lab
            WITH lab, entity
            WHERE NOT lab IN ['__Entity__', '__KGBuilder__']
            WITH lab, collect({{ id: elementId(entity), {props_map} }}) AS labelCluster
            RETURN lab, labelCluster
        """

        records, _, _ = self.driver.execute_query(query, database_=self.neo4j_database)

        total_entities = 0
        total_merged_nodes = 0

        # Step 2 (goldenmatch, per label): build merge sets via goldenmatch.
        for row in records:
            entities = row["labelCluster"]
            label = row["lab"]

            # Build (id, name, label) triples for resolve_records.
            items: list[tuple[str, str, str]] = []
            for ent in entities:
                texts = [
                    str(ent[p]) for p in self.resolve_properties if p in ent and ent[p]
                ]
                combined_text = " ".join(texts).strip()
                if combined_text:
                    items.append((ent["id"], combined_text, label))

            total_entities += len(items)

            if not items:
                continue

            # goldenmatch groups -> merge sets (only multi-member).
            merge_sets = resolve_records(items)

            # Step 3: APOC merge (same as base).
            merged_count = 0
            for group in merge_sets:
                if len(group) > 1:
                    merge_query = (
                        "MATCH (n) WHERE elementId(n) IN $ids "
                        "WITH collect(n) AS nodes "
                        "CALL apoc.refactor.mergeNodes(nodes, {properties: 'discard', mergeRels: true}) "
                        "YIELD node RETURN elementId(node)"
                    )
                    result, _, _ = self.driver.execute_query(
                        merge_query,
                        {"ids": list(group)},
                        database_=self.neo4j_database,
                    )
                    merged_count += len(result)
            total_merged_nodes += merged_count

        return ResolutionStats(
            number_of_nodes_to_resolve=total_entities,
            number_of_created_nodes=total_merged_nodes,
        )

    def resolve_entities_for_test(
        self, items: list[tuple[str, str, str]]
    ) -> list[list[str]]:
        """Test seam: run the goldenmatch-backed resolution decision in-process.

        Exercises the SAME goldenmatch path the overridden run() uses for the
        clustering decision, without touching Neo4j (driver not used here).

        Args:
            items: list of (id, name, label) triples, one per entity mention.

        Returns:
            Full partition: multi-member groups first, then singletons. Each group
            is a list of ids.
        """
        groups = resolve_records(items)
        seen: set[str] = {rid for g in groups for rid in g}
        singletons = [[rid] for rid, _name, _label in items if rid not in seen]
        return groups + singletons
