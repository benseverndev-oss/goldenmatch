# goldenmatch-kg: neo4j-graphrag

A real `GoldenMatchResolver` that drops goldenmatch in as the entity-resolution stage of a
neo4j-graphrag pipeline. It subclasses the library's own resolver component
(`BasePropertySimilarityResolver`) and overrides the resolution step (`run()`), so it is a
true in-pipeline plugin: goldenmatch decides which nodes merge, Neo4j persists the merges
via the same APOC path the built-in resolvers use. It replaces `FuzzyMatchResolver`.

## Install

```bash
pip install "goldenmatch-kg[neo4j-graphrag]"
```

## Use

```python
from goldenmatch_kg.neo4j_graphrag import GoldenMatchResolver

# Same construction as the built-in FuzzyMatchResolver: pass your Neo4j driver.
resolver = GoldenMatchResolver(driver=driver)

# Use it as the resolver component of your neo4j-graphrag pipeline (e.g. SimpleKGPipeline),
# in the slot where you would otherwise pass FuzzyMatchResolver / SinglePropertyExactMatchResolver.
# On run(), goldenmatch zero-config dedupe replaces the pairwise WRatio>=0.8 decision; the
# driver is used only for the Cypher fetch + the APOC merge (the clustering decision is in-process).
```

The clustering decision is exercised in-process and is driver-free, so it is unit-testable
with a mock driver (`resolver.resolve_entities_for_test([(id, name, label), ...])`).

## The lift

On the ER-KG-Bench ghsuite corpus, neo4j-graphrag's own fuzzy resolver (its real decision
code, run in-process) scores **F1 0.322**; goldenmatch zero-config scores **F1 0.969**. This
adapter delivers that 0.969 resolution through neo4j-graphrag's pipeline -- about **+64.7pp**
on the same corpus, at zero LLM calls. See `RESULTS_ghsuite.md` in the bench for the
side-by-side board (and `RESULTS.md` for the Wikidata/RxNorm corpus).
