# ER-KG-Bench results

Dataset: **105 records / 32 entities / 9 failure classes**. Embedder: `none (string predicates only)`.

`*` = precision-critical negative class (distinct entities with colliding surface forms; lower precision = wrong merges).

## Headline (pairwise, full set)

| System | P | R | F1 | coll&nbsp;P* | temp&nbsp;P* | ms | det-floor |
|---|---|---|---|---|---|---|---|
| goldenmatch(auto) | 0.74 | 0.291 | **0.418** | 0.333 | 0.0 | 237.0 | yes |
| goldenmatch(auto+fields) | 0.624 | 0.732 | **0.674** | 0.37 | 0.353 | 1101.0 | yes |
| MS-GraphRAG | 0.722 | 0.102 | **0.179** | 0.0 | 1.0 | 0.1 | yes |
| LightRAG | 0.722 | 0.102 | **0.179** | 0.0 | 1.0 | 0.0 | yes |
| Cognee | 0.722 | 0.102 | **0.179** | 0.0 | 1.0 | 0.0 | yes |
| mem0 | 0.812 | 0.102 | **0.182** | 0.0 | 1.0 | 0.2 | yes |
| Neo4j-KGBuilder | 0.782 | 0.535 | **0.636** | 0.333 | 0.0 | 2.1 | yes |
| neo4j-graphrag(fuzzy) | 0.444 | 0.717 | **0.548** | 0.389 | 0.381 | 14.9 | yes |
| LlamaIndex-PGI | 0.374 | 0.693 | **0.486** | 0.357 | 0.286 | 1.8 | yes |

## Per-class F1

| System | abbr | nick | synm | coll* | xling | typo | suffix | temp* | xdoc |
|---|---|---|---|---|---|---|---|---|---|
| goldenmatch(auto) | 0.462 | 0.235 | 0.0 | 0.267 | 0.2 | 0.75 | 0.37 | 0.0 | 1.0 |
| goldenmatch(auto+fields) | 0.667 | 0.75 | 0.2 | 0.444 | 0.839 | 1.0 | 1.0 | 0.48 | 1.0 |
| MS-GraphRAG | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.125 | 0.0 | 0.0 | 1.0 |
| LightRAG | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.125 | 0.0 | 0.0 | 1.0 |
| Cognee | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.125 | 0.0 | 0.0 | 1.0 |
| mem0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.125 | 0.0 | 0.0 | 1.0 |
| Neo4j-KGBuilder | 0.182 | 0.421 | 0.0 | 0.333 | 0.615 | 1.0 | 1.0 | 0.0 | 1.0 |
| neo4j-graphrag(fuzzy) | 0.571 | 0.8 | 0.0 | 0.519 | 0.5 | 1.0 | 1.0 | 0.552 | 1.0 |
| LlamaIndex-PGI | 0.385 | 0.636 | 0.0 | 0.435 | 0.455 | 1.0 | 1.0 | 0.267 | 1.0 |

## Documented defaults (what each row runs)

- **goldenmatch(auto)** — zero-config dedupe_df(name) -- auto-config picks the strategy
- **goldenmatch(auto+fields)** — zero-config dedupe_df(name+type+context) -- auto-config, multi-field
- **MS-GraphRAG** — exact title match; ER step removed (finalize_entities seen_titles set; discussion #778, data-loss #1718)
- **LightRAG** — exact normalized-name dict key, no fuzzy/embedding at merge (operate.py _merge_nodes_then_upsert; #1323, cross-doc #485)
- **Cognee** — content-hash + exact name; difflib cutoff=0.8 only vs a user ontology (empty by default) (matching_strategies.py; #1831)
- **mem0** — MD5-exact only as hard dedup; semantic merge is one LLM ADD/UPDATE prompt (main.py md5; contradictions #4896, 37.6% near-dupes #4573)
- **Neo4j-KGBuilder** — same-label gate AND ( substring-contains(len>2) OR Levenshtein<3(len>5) OR cosine>0.97 ); human-review-gated (graphDB_dataAccess.py; over-merge #1133, missed alias #912)
- **neo4j-graphrag(fuzzy)** — FuzzyMatchResolver: rapidfuzz WRatio/100 >= 0.8, all-pairs O(n^2) (resolver.py BasePropertySimilarityResolver; rapidfuzz extra #336)
- **LlamaIndex-PGI** — same-label gate AND ( contains OR Levenshtein<5 OR cosine>0.9 ), KNN top-10 when embedded (property-graph blog; self-documented over-merges: 1963 AFL/NFL, BTC Halving 2020/2024)

> Modelled rows reproduce each framework's deterministic default rule (exact constants + source in `adapters/modeled.py`). LLM-judge layers (Graphiti, mem0) are out of scope: non-deterministic, O(n)-in-LLM-calls, and ~$0.80/40-chats — see the module docstring.
