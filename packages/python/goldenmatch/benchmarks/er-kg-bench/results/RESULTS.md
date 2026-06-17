# ER-KG-Bench results

Dataset: **206 records / 48 entities / 9 failure classes**. Embedder: `st`.

`*` = precision-critical negative class (distinct entities with colliding surface forms; lower precision = wrong merges).

## Headline (pairwise, full set)

| System | P | R | F1 | fid | coll&nbsp;P* | temp&nbsp;P* | ms | det-floor |
|---|---|---|---|---|---|---|---|---|
| goldenmatch(auto) | 0.849 | 0.374 | **0.52** | real | 0.438 | 0.4 | 984.6 | yes |
| goldenmatch(auto+fields) | 0.786 | 0.488 | **0.602** | real | 0.471 | 0.4 | 4878.1 | yes |
| goldenmatch(emb-ann) | 0.455 | 0.426 | **0.44** | real | 0.471 | 0.4 | 65.9 | yes |
| MS-GraphRAG | 0.875 | 0.034 | **0.066** | modeled | 0.0 | 1.0 | 0.2 | yes |
| LightRAG | 0.875 | 0.034 | **0.066** | modeled | 0.0 | 1.0 | 0.1 | yes |
| Cognee | 0.875 | 0.034 | **0.066** | modeled | 0.0 | 1.0 | 0.1 | yes |
| mem0 | 0.875 | 0.034 | **0.066** | validated | 0.0 | 1.0 | 0.3 | yes |
| Neo4j-KGBuilder | 0.826 | 0.315 | **0.456** | modeled | 0.448 | 0.286 | 12.3 | yes |
| neo4j-graphrag(fuzzy) | 0.345 | 0.485 | **0.403** | modeled | 0.451 | 0.4 | 63.6 | yes |
| LlamaIndex-PGI | 0.141 | 0.51 | **0.221** | modeled | 0.452 | 0.286 | 7.0 | yes |
| Neo4j-KGBuilder(emb) | 0.795 | 0.335 | **0.471** | modeled | 0.448 | 0.4 | 654.2 | yes |
| LlamaIndex-PGI(emb) | 0.149 | 0.547 | **0.234** | modeled | 0.452 | 0.4 | 671.3 | yes |
| neo4j-graphrag(fuzzy)* | 0.477 | 0.461 | **0.469** | real-inproc | 0.471 | 0.4 | 12.6 | yes |
| neo4j-graphrag(exact) | 0.875 | 0.034 | **0.066** | validated | 0.0 | 1.0 | 0.2 | yes |
| neo4j-graphrag(spacy)* | 0.699 | 0.281 | **0.401** | real-inproc | 0.455 | 0.364 | 1943.2 | yes |

## Per-class F1

| System | abbr | nick | synm | coll* | xling | typo | suffix | temp* | xdoc |
|---|---|---|---|---|---|---|---|---|---|
| goldenmatch(auto) | 0.412 | 0.741 | 0.089 | 0.318 | 0.4 | 0.875 | 1.0 | 0.571 | 1.0 |
| goldenmatch(auto+fields) | 0.773 | 0.854 | 0.167 | 0.356 | 0.769 | 1.0 | 1.0 | 0.571 | 1.0 |
| goldenmatch(emb-ann) | 0.214 | 0.786 | 0.138 | 0.516 | 0.4 | 0.667 | 0.444 | 0.571 | 1.0 |
| MS-GraphRAG | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 1.0 |
| LightRAG | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 1.0 |
| Cognee | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 1.0 |
| mem0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 1.0 |
| Neo4j-KGBuilder | 0.412 | 0.406 | 0.034 | 0.456 | 0.588 | 0.714 | 1.0 | 0.308 | 1.0 |
| neo4j-graphrag(fuzzy) | 0.898 | 0.854 | 0.078 | 0.582 | 0.345 | 0.667 | 0.444 | 0.571 | 1.0 |
| LlamaIndex-PGI | 0.533 | 0.478 | 0.093 | 0.543 | 0.405 | 0.667 | 0.706 | 0.308 | 0.571 |
| Neo4j-KGBuilder(emb) | 0.412 | 0.406 | 0.034 | 0.456 | 0.588 | 0.714 | 1.0 | 0.571 | 1.0 |
| LlamaIndex-PGI(emb) | 0.533 | 0.622 | 0.093 | 0.543 | 0.405 | 0.667 | 0.706 | 0.571 | 0.571 |
| neo4j-graphrag(fuzzy)* | 0.898 | 0.854 | 0.045 | 0.516 | 0.345 | 0.667 | 0.444 | 0.571 | 1.0 |
| neo4j-graphrag(exact) | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 1.0 |
| neo4j-graphrag(spacy)* | 0.457 | 0.8 | 0.104 | 0.256 | 0.0 | 0.0 | 0.868 | 0.471 | 1.0 |

## Documented defaults (what each row runs)

- **goldenmatch(auto)** — zero-config dedupe_df(name) -- auto-config picks the strategy
- **goldenmatch(auto+fields)** — zero-config dedupe_df(name+type+context) -- auto-config, multi-field
- **goldenmatch(emb-ann)** — inhouse char-ngram embedding (no key/torch) -> cosine>=0.5 candidate pairs (ANN at scale) -> union-find; name only
- **MS-GraphRAG** — exact title match; ER step removed (finalize_entities seen_titles set, title=clean_str(name.upper()); discussion #778, data-loss #1718)
- **LightRAG** — exact case-SENSITIVE name dict key, outer-quote strip + CJK fold, no fuzzy/embedding at merge (operate.py _merge_nodes_then_upsert + normalize_extracted_info; #1323, cross-doc #485)
- **Cognee** — exact generate_node_name key = name.lower().replace("'",""); difflib cutoff=0.8 only vs a user ontology (empty by default) (generate_node_name.py / matching_strategies.py; #1831)
- **mem0** — MD5-exact only as hard dedup; semantic merge is one LLM ADD/UPDATE prompt (memory/main.py md5 over raw text; contradictions #4896, 37.6% near-dupes #4573)
- **Neo4j-KGBuilder** — same-label gate AND ( substring-contains(len>2) OR Levenshtein<3(len>5) OR cosine>0.97 ); human-review-gated (graphDB_dataAccess.py get_duplicate_nodes Cypher; over-merge #1133, missed alias #912)
- **neo4j-graphrag(fuzzy)** — FuzzyMatchResolver: rapidfuzz WRatio/100 >= 0.8, all-pairs O(n^2) (resolver.py BasePropertySimilarityResolver; rapidfuzz extra #336)
- **LlamaIndex-PGI** — same-label gate AND ( contains OR Levenshtein<5 OR cosine>0.9 ), KNN top-10 when embedded (property-graph blog; self-documented over-merges: 1963 AFL/NFL, BTC Halving 2020/2024)
- **Neo4j-KGBuilder(emb)** — same-label gate AND ( substring-contains(len>2) OR Levenshtein<3(len>5) OR cosine>0.97 ); human-review-gated (graphDB_dataAccess.py get_duplicate_nodes Cypher; over-merge #1133, missed alias #912)
- **LlamaIndex-PGI(emb)** — same-label gate AND ( contains OR Levenshtein<5 OR cosine>0.9 ), KNN top-10 when embedded (property-graph blog; self-documented over-merges: 1963 AFL/NFL, BTC Halving 2020/2024)
- **neo4j-graphrag(fuzzy)*** — REAL FuzzyMatchResolver: rapidfuzz WRatio/100>=0.8 per entity-label, _consolidate_sets (library decision code; Neo4j+APOC storage stubbed)
- **neo4j-graphrag(exact)** — SinglePropertyExactMatchResolver: exact `name` equality per entity-label, null names skipped (logic is a Cypher query in run(); no in-process decision method exists, so this is the Cypher re-expressed + confirmed -> validated)
- **neo4j-graphrag(spacy)*** — REAL SpaCySemanticMatchResolver: spaCy doc-vector cosine >= 0.8 per entity-label, _consolidate_sets (library decision code; en_core_web_lg vectors; Neo4j+APOC storage stubbed)

> Modelled rows reproduce each framework's deterministic default rule (exact constants + source in `adapters/modeled.py`). LLM-judge layers (Graphiti, mem0) are out of scope: non-deterministic, O(n)-in-LLM-calls, and ~$0.80/40-chats — see the module docstring.
