# ER-KG-Bench results

Dataset: **129 records / 45 entities / 9 failure classes**. Embedder: `st`.

`*` = precision-critical negative class (distinct entities with colliding surface forms; lower precision = wrong merges).

## Headline (pairwise, full set)

| System | P | R | F1 | fid | coll&nbsp;P* | temp&nbsp;P* | ms | det-floor | LLM? |
|---|---|---|---|---|---|---|---|---|---|
| goldenmatch(auto) | 0.931 | 0.513 | **0.661** | real | - | 1.0 | 330.1 | yes | no |
| goldenmatch(auto+fields) | 0.963 | 0.975 | **0.969** | real | - | 1.0 | 3877.9 | yes | no |
| goldenmatch(emb-ann) | 0.587 | 0.576 | **0.581** | real | - | 1.0 | 12.5 | yes | no |
| mem0 | 1.0 | 0.0 | **0.0** | validated | - | 1.0 | 0.2 | yes | no |
| Neo4j-KGBuilder | 0.812 | 0.494 | **0.614** | modeled | - | 1.0 | 15.4 | yes | no |
| neo4j-graphrag(fuzzy) | 0.209 | 0.703 | **0.322** | modeled | - | 1.0 | 26.1 | yes | no |
| LlamaIndex-PGI | 0.051 | 0.81 | **0.095** | modeled | - | 0.333 | 11.8 | yes | no |
| Neo4j-KGBuilder(emb) | 0.812 | 0.494 | **0.614** | modeled | - | 1.0 | 631.9 | yes | no |
| LlamaIndex-PGI(emb) | 0.051 | 0.81 | **0.095** | modeled | - | 0.333 | 624.9 | yes | no |
| MS-GraphRAG | 1.0 | 0.063 | **0.119** | validated | - | 1.0 | 0.2 | yes | no |
| Cognee | 1.0 | 0.139 | **0.244** | validated | - | 1.0 | 0.7 | yes | no |
| neo4j-graphrag(fuzzy)* | 0.209 | 0.703 | **0.322** | real-inproc | - | 1.0 | 17.7 | yes | no |
| neo4j-graphrag(exact) | 1.0 | 0.0 | **0.0** | validated | - | 1.0 | 0.1 | yes | no |
| neo4j-graphrag(spacy)* | 0.917 | 0.139 | **0.242** | real-inproc | - | 1.0 | 1580.3 | yes | no |
| LightRAG* | 1.0 | 0.0 | **0.0** | real-inproc | - | 1.0 | 7.7 | yes | no |
| graphiti* | 1.0 | 0.234 | **0.379** | real-inproc | - | 1.0 | 1904.3 | yes | no |

## Per-class F1

| System | abbr | nick | synm | coll* | xling | typo | suffix | temp* | xdoc |
|---|---|---|---|---|---|---|---|---|---|
| goldenmatch(auto) | 0.0 | 0.88 | 0.333 | - | - | 1.0 | - | 1.0 | 0.0 |
| goldenmatch(auto+fields) | 1.0 | 0.966 | 1.0 | - | - | 1.0 | - | 1.0 | 0.0 |
| goldenmatch(emb-ann) | 0.0 | 0.71 | 0.0 | - | - | 0.8 | - | 1.0 | 0.0 |
| mem0 | 0.0 | 0.0 | 0.0 | - | - | 0.0 | - | 0.0 | 1.0 |
| Neo4j-KGBuilder | 0.0 | 0.64 | 0.0 | - | - | 1.0 | - | 1.0 | 0.0 |
| neo4j-graphrag(fuzzy) | 0.667 | 0.364 | 0.0 | - | - | 0.286 | - | 1.0 | 0.0 |
| LlamaIndex-PGI | 0.036 | 0.123 | 0.04 | - | - | 0.125 | - | 0.5 | 0.0 |
| Neo4j-KGBuilder(emb) | 0.0 | 0.64 | 0.0 | - | - | 1.0 | - | 1.0 | 0.0 |
| LlamaIndex-PGI(emb) | 0.036 | 0.123 | 0.04 | - | - | 0.125 | - | 0.5 | 0.0 |
| MS-GraphRAG | 0.0 | 0.133 | 0.0 | - | - | 0.0 | - | 0.0 | 1.0 |
| Cognee | 0.0 | 0.133 | 0.0 | - | - | 0.0 | - | 0.0 | 1.0 |
| neo4j-graphrag(fuzzy)* | 0.667 | 0.364 | 0.0 | - | - | 0.286 | - | 1.0 | 0.0 |
| neo4j-graphrag(exact) | 0.0 | 0.0 | 0.0 | - | - | 0.0 | - | 0.0 | 1.0 |
| neo4j-graphrag(spacy)* | 0.0 | 0.133 | 0.0 | - | - | 0.0 | - | 0.0 | 0.0 |
| LightRAG* | 0.0 | 0.0 | 0.0 | - | - | 0.0 | - | 0.0 | 1.0 |
| graphiti* | 0.0 | 0.353 | 0.0 | - | - | 0.667 | - | 1.0 | 1.0 |

## Documented defaults (what each row runs)

- **goldenmatch(auto)** — zero-config dedupe_df(name) -- auto-config picks the strategy
- **goldenmatch(auto+fields)** — zero-config dedupe_df(name+type+context) -- auto-config, multi-field
- **goldenmatch(emb-ann)** — inhouse char-ngram embedding (no key/torch) -> cosine>=0.5 candidate pairs (ANN at scale) -> union-find; name only
- **mem0** — MD5-exact only as hard dedup; semantic merge is one LLM ADD/UPDATE prompt (memory/main.py md5 over raw text; contradictions #4896, 37.6% near-dupes #4573)
- **Neo4j-KGBuilder** — same-label gate AND ( substring-contains(len>2) OR Levenshtein<3(len>5) OR cosine>0.97 ); human-review-gated (graphDB_dataAccess.py get_duplicate_nodes Cypher; over-merge #1133, missed alias #912)
- **neo4j-graphrag(fuzzy)** — FuzzyMatchResolver: rapidfuzz WRatio/100 >= 0.8, all-pairs O(n^2) (resolver.py BasePropertySimilarityResolver; rapidfuzz extra #336)
- **LlamaIndex-PGI** — same-label gate AND ( contains OR Levenshtein<5 OR cosine>0.9 ), KNN top-10 when embedded (property-graph blog; self-documented over-merges: 1963 AFL/NFL, BTC Halving 2020/2024)
- **Neo4j-KGBuilder(emb)** — same-label gate AND ( substring-contains(len>2) OR Levenshtein<3(len>5) OR cosine>0.97 ); human-review-gated (graphDB_dataAccess.py get_duplicate_nodes Cypher; over-merge #1133, missed alias #912)
- **LlamaIndex-PGI(emb)** — same-label gate AND ( contains OR Levenshtein<5 OR cosine>0.9 ), KNN top-10 when embedded (property-graph blog; self-documented over-merges: 1963 AFL/NFL, BTC Halving 2020/2024)
- **MS-GraphRAG** — exact title-set merge, GLOBAL (not per-label): title=clean_str(name.upper()) -> upper + edge-strip + html-unescape + control-char strip, NO internal-ws collapse (finalize_entities seen_titles + graph_extractor + utils/string.py; Standard pipeline has no ER step -> validated reproduction, no separable resolver)
- **Cognee** — exact merge on generate_node_id = uuid5(NAMESPACE_OID, name.lower().replace(' ','_').replace("'",'')), GLOBAL (not per-label); default ontology empty so the difflib cutoff never fires (generate_node_id.py + deduplicate_nodes_and_edges.py -> validated)
- **neo4j-graphrag(fuzzy)*** — REAL FuzzyMatchResolver: rapidfuzz WRatio/100>=0.8 per entity-label, _consolidate_sets (library decision code; Neo4j+APOC storage stubbed)
- **neo4j-graphrag(exact)** — SinglePropertyExactMatchResolver: exact `name` equality per entity-label, null names skipped (logic is a Cypher query in run(); no in-process decision method exists, so this is the Cypher re-expressed + confirmed -> validated)
- **neo4j-graphrag(spacy)*** — REAL SpaCySemanticMatchResolver: spaCy doc-vector cosine >= 0.8 per entity-label, _consolidate_sets (library decision code; en_core_web_lg vectors; Neo4j+APOC storage stubbed)
- **LightRAG*** — REAL normalize_extracted_info key (HTML-strip, CJK fold, outer-quote strip, CASE-SENSITIVE -- no lower/upper) + exact name dict group-by, GLOBAL (operate.py merge_nodes_and_edges + utils.py; LLM only summarizes descriptions, never moves a record; graph-store upsert stubbed)
- **graphiti*** — REAL deterministic floor: exact normalized-name (lower+ws-collapse) OR MinHash/Jaccard>=0.9, with a low-entropy/short-name gate (_resolve_with_similarity + _build_candidate_indexes, dedup_helpers.py); sequential ingestion vs the growing existing set. DETERMINISTIC FLOOR ONLY -- the full default path escalates unresolved nodes to an LLM (out of scope)

> Each row carries a `fid` tier (see `adapters/FIDELITY.md`): `real-inproc` runs the framework's real decision code; `validated` reproduces its exact rule confirmed vs source; `modeled` is an unconfirmed/divergent re-impl. mem0's LLM ADD/UPDATE merge layer stays out of scope (non-deterministic, per-pair LLM cost; Phase 3) -- this board runs each framework's deterministic dedup, including Graphiti's MinHash/Jaccard floor.
