# ER-KG-Bench results

Dataset: **171 records / 54 entities / 9 failure classes**. Embedder: `none (string predicates only)`.

`*` = precision-critical negative class (distinct entities with colliding surface forms; lower precision = wrong merges).

## Headline (pairwise, full set)

| System | P | R | F1 | coll&nbsp;P* | temp&nbsp;P* | ms | det-floor |
|---|---|---|---|---|---|---|---|
| goldenmatch(auto) | 0.735 | 0.362 | **0.485** | 0.368 | 0.267 | 866.2 | yes |
| goldenmatch(auto+fields) | 0.556 | 0.724 | **0.629** | 0.375 | 0.368 | 2654.3 | yes |
| goldenmatch(emb-ann) | 0.372 | 0.673 | **0.479** | 0.392 | 0.368 | 68.4 | yes |
| MS-GraphRAG | 0.708 | 0.085 | **0.152** | 0.0 | 1.0 | 0.2 | yes |
| LightRAG | 0.708 | 0.085 | **0.152** | 0.0 | 1.0 | 0.1 | yes |
| Cognee | 0.708 | 0.085 | **0.152** | 0.0 | 1.0 | 0.1 | yes |
| mem0 | 0.773 | 0.085 | **0.154** | 0.0 | 1.0 | 0.3 | yes |
| Neo4j-KGBuilder | 0.703 | 0.513 | **0.593** | 0.353 | 0.25 | 10.1 | yes |
| neo4j-graphrag(fuzzy) | 0.248 | 0.698 | **0.366** | 0.393 | 0.381 | 53.0 | yes |
| LlamaIndex-PGI | 0.144 | 0.673 | **0.237** | 0.293 | 0.3 | 5.5 | yes |

## Per-class F1

| System | abbr | nick | synm | coll* | xling | typo | suffix | temp* | xdoc |
|---|---|---|---|---|---|---|---|---|---|
| goldenmatch(auto) | 0.414 | 0.552 | 0.0 | 0.286 | 0.25 | 0.857 | 0.512 | 0.258 | 1.0 |
| goldenmatch(auto+fields) | 0.409 | 0.833 | 0.105 | 0.462 | 0.865 | 1.0 | 0.762 | 0.519 | 1.0 |
| goldenmatch(emb-ann) | 0.133 | 0.95 | 0.0 | 0.494 | 0.552 | 1.0 | 0.496 | 0.519 | 1.0 |
| MS-GraphRAG | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.154 | 0.0 | 0.0 | 1.0 |
| LightRAG | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.154 | 0.0 | 0.0 | 1.0 |
| Cognee | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.154 | 0.0 | 0.0 | 1.0 |
| mem0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.154 | 0.0 | 0.0 | 1.0 |
| Neo4j-KGBuilder | 0.154 | 0.5 | 0.0 | 0.375 | 0.6 | 0.933 | 1.0 | 0.25 | 1.0 |
| neo4j-graphrag(fuzzy) | 0.25 | 0.865 | 0.0 | 0.527 | 0.5 | 1.0 | 0.496 | 0.552 | 1.0 |
| LlamaIndex-PGI | 0.175 | 0.765 | 0.0 | 0.386 | 0.353 | 1.0 | 0.533 | 0.333 | 1.0 |

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
