# ER-KG-Bench results

Dataset: **147 records / 33 entities / 9 failure classes**. Embedder: `none (string predicates only)`.

`*` = precision-critical negative class (distinct entities with colliding surface forms; lower precision = wrong merges).

## Headline (pairwise, full set)

| System | P | R | F1 | coll&nbsp;P* | temp&nbsp;P* | ms | det-floor |
|---|---|---|---|---|---|---|---|
| goldenmatch(auto) | 0.844 | 0.487 | **0.617** | 0.438 | 0.4 | 762.4 | yes |
| goldenmatch(auto+fields) | 0.869 | 0.617 | **0.721** | 0.471 | 0.4 | 2421.0 | yes |
| goldenmatch(emb-ann) | 0.447 | 0.547 | **0.492** | 0.471 | 0.4 | 49.5 | yes |
| MS-GraphRAG | 0.875 | 0.047 | **0.089** | 0.0 | 1.0 | 0.1 | yes |
| LightRAG | 0.875 | 0.047 | **0.089** | 0.0 | 1.0 | 0.1 | yes |
| Cognee | 0.875 | 0.047 | **0.089** | 0.0 | 1.0 | 0.1 | yes |
| mem0 | 0.875 | 0.047 | **0.089** | 0.0 | 1.0 | 0.2 | yes |
| Neo4j-KGBuilder | 0.828 | 0.417 | **0.554** | 0.448 | 0.286 | 5.6 | yes |
| neo4j-graphrag(fuzzy) | 0.346 | 0.637 | **0.448** | 0.451 | 0.4 | 38.7 | yes |
| LlamaIndex-PGI | 0.219 | 0.56 | **0.315** | 0.452 | 0.286 | 3.4 | yes |

## Per-class F1

| System | abbr | nick | synm | coll* | xling | typo | suffix | temp* | xdoc |
|---|---|---|---|---|---|---|---|---|---|
| goldenmatch(auto) | 0.412 | 0.741 | 0.06 | 0.318 | 0.4 | 0.875 | 1.0 | 0.571 | 1.0 |
| goldenmatch(auto+fields) | 0.773 | 0.854 | 0.141 | 0.356 | 0.769 | 1.0 | 1.0 | 0.571 | 1.0 |
| goldenmatch(emb-ann) | 0.214 | 0.786 | 0.116 | 0.516 | 0.4 | 0.667 | 0.444 | 0.571 | 1.0 |
| MS-GraphRAG | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 1.0 |
| LightRAG | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 1.0 |
| Cognee | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 1.0 |
| mem0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 1.0 |
| Neo4j-KGBuilder | 0.412 | 0.406 | 0.0 | 0.456 | 0.588 | 0.714 | 1.0 | 0.308 | 1.0 |
| neo4j-graphrag(fuzzy) | 0.898 | 0.854 | 0.03 | 0.582 | 0.345 | 0.667 | 0.444 | 0.571 | 1.0 |
| LlamaIndex-PGI | 0.533 | 0.478 | 0.055 | 0.543 | 0.405 | 0.667 | 0.706 | 0.308 | 0.571 |

## Documented defaults (what each row runs)

- **goldenmatch(auto)** — zero-config dedupe_df(name) -- auto-config picks the strategy
- **goldenmatch(auto+fields)** — zero-config dedupe_df(name+type+context) -- auto-config, multi-field
- **goldenmatch(emb-ann)** — inhouse char-ngram embedding (no key/torch) -> cosine>=0.5 candidate pairs (ANN at scale) -> union-find; name only
- **MS-GraphRAG** — exact title match; ER step removed (finalize_entities seen_titles set; discussion #778, data-loss #1718)
- **LightRAG** — exact normalized-name dict key, no fuzzy/embedding at merge (operate.py _merge_nodes_then_upsert; #1323, cross-doc #485)
- **Cognee** — content-hash + exact name; difflib cutoff=0.8 only vs a user ontology (empty by default) (matching_strategies.py; #1831)
- **mem0** — MD5-exact only as hard dedup; semantic merge is one LLM ADD/UPDATE prompt (main.py md5; contradictions #4896, 37.6% near-dupes #4573)
- **Neo4j-KGBuilder** — same-label gate AND ( substring-contains(len>2) OR Levenshtein<3(len>5) OR cosine>0.97 ); human-review-gated (graphDB_dataAccess.py; over-merge #1133, missed alias #912)
- **neo4j-graphrag(fuzzy)** — FuzzyMatchResolver: rapidfuzz WRatio/100 >= 0.8, all-pairs O(n^2) (resolver.py BasePropertySimilarityResolver; rapidfuzz extra #336)
- **LlamaIndex-PGI** — same-label gate AND ( contains OR Levenshtein<5 OR cosine>0.9 ), KNN top-10 when embedded (property-graph blog; self-documented over-merges: 1963 AFL/NFL, BTC Halving 2020/2024)

> Modelled rows reproduce each framework's deterministic default rule (exact constants + source in `adapters/modeled.py`). LLM-judge layers (Graphiti, mem0) are out of scope: non-deterministic, O(n)-in-LLM-calls, and ~$0.80/40-chats — see the module docstring.
