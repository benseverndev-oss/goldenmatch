# Failure-mode taxonomy

Nine classes of entity-mention variation, each chosen because at least one
shipping knowledge-graph / agent-memory framework demonstrably mis-handles it
at its documented defaults. Citations point at the framework's own source or
issue tracker — the benchmark asserts nothing the maintainers haven't already
documented.

| Class | Example (same entity unless noted) | Who breaks, and how |
|---|---|---|
| `abbreviation` | IBM ↔ International Business Machines | All string methods miss it (no overlap below a 0.9/0.97 cosine cutoff). GraphRAG "IT"↔"Information Technology" stay distinct. |
| `nickname_alias` | Bucky Barnes ↔ James Buchanan Barnes | Neo4j builder #912 leaves all three as separate nodes (closed *not planned*). |
| `synonym_brand` | Coumadin ↔ warfarin | No char-based method is semantic; LightRAG won't merge, Cognee's difflib can't. |
| `same_name_collision` ⚠ | "First National Bank" (Algeria) vs (USA) — **distinct entities** | Exact-match systems **over-merge** (Neo4j #1133, GraphRAG #1718 *loses data*). Precision test. |
| `cross_lingual` | Munich ↔ München ↔ Monaco di Baviera | All char/edit methods fail; Graphiti's LLM dedup **infinite-loops to the token cap** on French M&A text (#760). |
| `typo` | Warfarin ↔ Warfrin | Caught only by edit-distance / rapidfuzz; exact-match systems miss. |
| `org_suffix` | Acme ↔ Acme Inc ↔ Acme Incorporated | Substring-containment catches some; exact-match misses. |
| `temporal_version` ⚠ | BTC Halving 2020 vs 2024; 1963 AFL vs NFL Draft — **distinct** | LlamaIndex's **own blog** shows it collapsing these (1-char apart). Precision test. |
| `cross_document_exact` | "Stark Industries" across several ingests | LightRAG #485 duplicates across `insert` calls; Graphiti #875 duplicates under a custom DB name. |

⚠ = **negative test**: the surface forms collide but the entities are
different. The headline metric for these is **precision** — how often a
resolver *avoids* a wrong merge. A single similarity score cannot separate
"Apple"/"Apple Inc" (merge) from "Apple"/"Apple Corps" (don't); that is the
structural ceiling multi-field probabilistic ER exists to break.

## Why a single threshold cannot win

Neo4j's builder illustrates the "two-knob trap": its cosine threshold is
**0.97** (so strict it misses every abbreviation/synonym → false negatives), so
it ORs in edit-distance **< 3** and substring-containment (so loose they merge
distinct short entities → false positives). No setting of one scalar fixes
both. The LLM-judge route (Graphiti, mem0) escapes the threshold trap only by
becoming non-deterministic, O(n) in LLM calls, and costly.

## Source pointers

- MS GraphRAG: `finalize_entities` seen-titles set; ER removed (discussion #778); data-loss #1718.
- LightRAG: `operate.py` `_merge_nodes_then_upsert` exact-name; #1323, #485.
- Cognee: `matching_strategies.py` `FuzzyMatchingStrategy(cutoff=0.8)` vs ontology (empty by default); #1831.
- mem0: `main.py` MD5 hash + LLM ADD/UPDATE; #4896, #4573 (37.6% near-dupes at scale).
- Graphiti/Zep: `dedup_helpers.py` MinHash/LSH (Jaccard ≥ 0.9) + entropy gate 1.5 + cosine 0.6 retrieval + LLM dedup prompt; #875, #630, #760, #1275, #1516.
- Neo4j builder: `graphDB_dataAccess.py` cosine 0.97 / edit-dist 3 / containment, human-gated; #1133, #912.
- neo4j-graphrag-python: `resolver.py` similarity_threshold 0.8 (spaCy / rapidfuzz WRatio), all-pairs.
- LlamaIndex: property-graph blog — KNN-10 + word-distance 5 + containment, threshold 0.9.
