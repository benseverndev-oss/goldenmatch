# Adapter fidelity audit (Phases 1-2)

> **Phase 2 update (see the dedicated section below the verdict table):** the
> deferred spaCy resolver shipped as a real row (`neo4j-graphrag(spacy)*`,
> `real-inproc`, F1 0.401); `--embedder st` adds two additive cosine-activated rows
> (`Neo4j-KGBuilder(emb)` 0.471, `LlamaIndex-PGI(emb)` 0.234, both `modeled`); the
> Neo4j-KGBuilder length-guard divergence was VERIFIED at source as `elementId`-sided
> (irreproducible, NOT a fixable min-vs-max gap); and a `_consolidate_sets` overlap bug
> was fixed (fuzzy real F1 0.470 -> 0.469).

Every adapter declares a `fidelity` tier so a reader can tell, at a glance, how
close a row is to the framework it claims to represent:

- **`real`** -- the actual system runs (the goldenmatch rows).
- **`real-inproc`** -- a real framework's actual Python decision code executes
  in-process (e.g. neo4j-graphrag's `FuzzyMatchResolver.compute_similarity` +
  `_consolidate_sets`); only the I/O (live Neo4j) is stubbed.
- **`validated`** -- a MODEL of the framework's rule, confirmed line-by-line
  against the framework's real merge source (predicate + constants +
  normalization all match) AND faithfully reproducing the real DEFAULT rule in
  full. The bar is deliberately high: a model that only matches part of the
  default rule (e.g. an OR-term inactive in our config) does NOT earn it.
- **`modeled`** -- a model that could NOT be confirmed against maintained
  source, OR that diverges from the real rule, OR that reproduces only part of
  the real default rule. A documented divergence is a finding, not a failure.

This audit read each framework's real merge/dedup source on GitHub and compared
it to our model.

> **Phase 3a update (real-execution cutover).** GraphRAG, LightRAG, and Cognee were
> originally modeled with a SHARED `_norm(s) = " ".join(s.lower().split())` that
> diverged from each framework's real key (each differently). They are now cut over
> to faithful rows in `adapters/real/`: **GraphRAG** and **Cognee** reproduce their
> exact key verbatim (`validated`, `exact_family.py`); **LightRAG** runs the
> library's real `normalize_extracted_info` key fn (`real-inproc`, `lightrag.py`);
> and **Graphiti** is a NEW `real-inproc` row running its real deterministic dedup
> floor. The shared `_norm` survives only inside `Neo4jBuilderModeled` /
> `LlamaIndexModeled`. The "matches our model?" column below is historical (it
> records WHY each was modeled); the live tier is in the rightmost column.

## Verdict table

| Framework | adapter | real source checked | exact real rule | matches our model? | tier |
|---|---|---|---|---|---|
| Microsoft GraphRAG | `RealGraphRAG` | [finalize_entities.py](https://github.com/microsoft/graphrag/blob/main/packages/graphrag/graphrag/index/operations/finalize_entities.py) + [graph_extractor.py](https://github.com/microsoft/graphrag/blob/main/packages/graphrag/graphrag/index/operations/extract_graph/graph_extractor.py) + [string.py](https://github.com/microsoft/graphrag/blob/main/packages/graphrag/graphrag/index/utils/string.py) | exact `title in seen_titles` (GLOBAL); `title = clean_str(name.upper())` (upper + `.strip()` + html-unescape + control-char strip; **no internal-ws collapse, no quote strip**). Standard pipeline has no ER step | reproduced VERBATIM (`real_resolvers._graphrag_key`). No separable resolver decision object exists, so this is the confirmed key, not a library run -> `validated` | **validated** |
| LightRAG | `RealLightRAG` (`*`) | [operate.py](https://github.com/HKUDS/LightRAG/blob/main/lightrag/operate.py) + [utils.py `normalize_extracted_info`](https://github.com/HKUDS/LightRAG/blob/main/lightrag/utils.py) | exact `entity_name` dict key (GLOBAL); name = real `normalize_extracted_info(.., remove_inner_quotes=True)` -> **case-PRESERVING** (no upper/lower), outer-quote strip, CJK fold, `.strip()`. LLM only summarizes descriptions, never moves a record | runs the library's REAL key fn in-process (only the graph-store upsert is elided; the merge decision IS the normalized-name equality) -> `real-inproc` | **real-inproc** |
| Cognee | `RealCognee` | [generate_node_id.py](https://github.com/topoteretes/cognee/blob/main/cognee/infrastructure/engine/utils/generate_node_id.py) + [deduplicate_nodes_and_edges.py](https://github.com/topoteretes/cognee/blob/main/cognee/modules/graph/utils/deduplicate_nodes_and_edges.py) + [get_default_ontology_resolver.py](https://github.com/topoteretes/cognee/blob/main/cognee/modules/ontology/get_default_ontology_resolver.py) | exact merge on `generate_node_id = uuid5(NAMESPACE_OID, name.lower().replace(" ","_").replace("'",""))` (GLOBAL). Default ontology empty (`ontology_file=None`) so the difflib cutoff never fires | reproduced VERBATIM (`real_resolvers._cognee_key`). **Fixes the Phase-1 bug**: it cited `generate_node_NAME` (display) + used `_norm`, missing the `" "->"_"` step. Pure stdlib uuid5 -> `validated` | **validated** |
| Graphiti | `RealGraphiti` (`*`) | [dedup_helpers.py](https://github.com/getzep/graphiti/blob/main/graphiti_core/utils/maintenance/dedup_helpers.py) (`_resolve_with_similarity` + `_build_candidate_indexes`) | DETERMINISTIC FLOOR: exact normalized-name (lower+ws-collapse) OR MinHash/Jaccard>=0.9, with a low-entropy/short-name gate; label-agnostic (no label gate) | runs the library's REAL floor decision code via sequential ingestion. Honest scope: no LLM/embedder (unresolved -> new entity); full existing set fed as candidates (upper bound on the floor). This is the FLOOR, not Graphiti's full LLM-backed default -> `real-inproc` | **real-inproc** |
| mem0 | `Mem0Modeled` | [memory/main.py](https://github.com/mem0ai/mem0/blob/main/mem0/memory/main.py) | `hashlib.md5(text.encode()).hexdigest()` over RAW memory text, case-sensitive, no normalization (`_add_to_vector_store` + `_create_memory`) | YES (byte-identical to our `md5(r.mention.encode())`) -- MD5 floor only; LLM ADD/UPDATE layer out of scope | **validated** |
| Neo4j LLM KG Builder | `Neo4jBuilderModeled` | [graphDB_dataAccess.py](https://github.com/neo4j-labs/llm-graph-builder/blob/main/backend/src/graphDB_dataAccess.py) | `labels(n)=labels(other)` AND ( CONTAINS guard `size>2` both dirs, `toLower` OR `apoc.text.distance < 3` guard `size(n.id)>5` OR `vector.similarity.cosine > 0.97` ); `DUPLICATE_TEXT_DISTANCE=3`, `DUPLICATE_SCORE_VALUE=0.97` | string predicates + constants confirmed, but default run is PARTIAL (cosine OR-term needs embedder) + an `elementId`-sided length guard that is IRREPRODUCIBLE by a commutative predicate (Phase-2 verified) | **modeled** |
| Neo4j-KGBuilder (emb) | `emb_modeled` (`--embedder st`) | same | adds the real cosine>0.97 OR-term (MiniLM); fires all 3 OR-branches | F1 0.456 -> 0.471 (+1.5pp, only `temporal`/`nick` move; dominant classes flat) but still `modeled` -- the `elementId` length guard remains irreproducible | **modeled** |
| neo4j-graphrag (fuzzy, model) | `Neo4jGraphRAGFuzzyModeled` | [resolver.py](https://github.com/neo4j/neo4j-graphrag-python/blob/main/src/neo4j_graphrag/experimental/components/resolver.py) | `fuzz.WRatio(a, b, processor=utils.default_process)/100 >= 0.8` | per-pair predicate matches, but MODEL F1 0.403 vs REAL in-proc F1 0.469 (-6.6pp) -> DIVERGENT in clustering | **modeled** |
| neo4j-graphrag (fuzzy, real) | `RealNeo4jGraphRAGFuzzy` (`*`) | same | real `compute_similarity` + `_consolidate_sets` (+ `_merge_overlapping`) per entity-label run in-process | runs the library's real decision code | **real-inproc** |
| neo4j-graphrag (spaCy, real) | `RealNeo4jGraphRAGSpaCy` (`*`) | same | real `SpaCySemanticMatchResolver.compute_similarity` (spaCy `en_core_web_lg` doc-vector cosine >= 0.8) + `_consolidate_sets` per entity-label, in-process | runs the library's real decision code; F1 0.401 (P 0.699 / R 0.281) | **real-inproc** |
| neo4j-graphrag (exact) | `RealNeo4jGraphRAGExact` | same | `SinglePropertyExactMatchResolver`: Cypher exact `name` equality per label, null skipped, NO normalization; no Python decision method | Cypher re-expressed + confirmed | **validated** |
| LlamaIndex PGI | `LlamaIndexModeled` | [llama_index property_graph](https://github.com/run-llama/llama_index/tree/main/llama-index-core/llama_index/core/indices/property_graph) + Bratanic property-graph blog | model: same-label AND (contains OR Levenshtein<5 OR cosine>0.9), KNN top-10 | constants come from a BLOG, not maintained source; library default is exact name+label upsert (no fuzzy dedup) -> CANNOT confirm | **modeled** |
| LlamaIndex PGI (emb) | `emb_modeled` (`--embedder st`) | same | adds the real cosine>0.9 OR-term (MiniLM) | F1 0.221 -> 0.234 (+1.3pp); tier unchanged -- an embedder does not close the blog-provenance gap | **modeled** |

## Per-framework detail

### 1. Microsoft GraphRAG -- `validated` (Phase 3a)

`finalize_entities` deduplicates by exact title:

```python
seen_titles: set[str] = set()
async for row in entities_table:
    title = row.get("title")
    if not title or title in seen_titles:
        continue
    seen_titles.add(title)
```

The title comes from extraction (`graph_extractor.py`):

```python
entity_name = clean_str(record_attributes[1].upper())   # <-- UPPERCASES
...
"title": entity_name,
```

`clean_str` (`index/utils/string.py`):

```python
result = html.unescape(input.strip())                    # leading/trailing strip only
return re.sub(r"[\x00-\x1f\x7f-\x9f]", "", result)        # control-char strip
```

So the real key is `clean_str(name.upper())`: uppercase, trailing/leading strip,
html-unescape, control-char strip. **No internal-whitespace collapse, no quote
strip.** Our `_norm` lowercases + collapses internal whitespace. The case
direction (upper vs lower) is clustering-equivalent (both fold every string to a
single canonical case). The **internal-whitespace collapse is a real divergence**:
`"New  York"` (two spaces) vs `"New York"` merge under our `_norm` but NOT under
GraphRAG.

**Phase 3a:** `RealGraphRAG` (`real_resolvers._graphrag_key` / `graphrag_clusters`)
reproduces `clean_str(name.upper())` VERBATIM (upper -> edge-strip -> html.unescape
-> control-char strip; GLOBAL `seen_titles` set, not per-label). GraphRAG has no
separable resolver decision object to run (the merge is `df.merge(on="title")` +
the `seen_titles` set inside the LLM-driven Standard pipeline), so the faithful
tier is **`validated`** -- a confirmed key reproduction, like `neo4j-graphrag(exact)`.
Observed F1 0.066 (exact-title recalls ~nothing on surface-form variation).

### 2. LightRAG -- `real-inproc` (Phase 3a)

`_merge_nodes_then_upsert` uses `entity_name` as the merge key with no further
transform; the name was normalized at extraction (`operate.py`):

```python
entity_name = sanitize_and_normalize_extracted_text(
    record_attributes[1], remove_inner_quotes=True
)
```

`normalize_extracted_info` (`utils.py`) strips OUTER quotes (`"`, `'`, CJK
quotes), folds CJK full-width chars to half-width, removes spaces between CJK
chars, and `.strip()`s -- but applies **no `.upper()`/`.lower()`** (only
`entity_type` gets `.replace(" ", "").lower()`, not the name). So LightRAG's
merge key is **case-SENSITIVE** (`"Apple"` != `"apple"`).

**Phase 3a:** `RealLightRAG` (`real_resolvers.lightrag_clusters`) runs the
library's REAL `normalize_extracted_info(name, remove_inner_quotes=True)` key fn
(lazy-imported `lightrag.utils`) then groups by the exact key GLOBALLY -- the same
dict group-by `merge_nodes_and_edges` does. The LLM only summarizes descriptions
(>=8 fragments) and never moves a record between clusters, so the clustering
decision runs with NO LLM/key. Tier **`real-inproc`** (the real key fn executes;
only the graph-store upsert is elided). CI-only (`lightrag-hku`). The low F1 it
produces is FAITHFUL to LightRAG's case-sensitive key, not a model artifact.

### 3. Cognee -- `validated` (Phase 3a)

The entity MERGE key is `generate_node_id` (NOT `generate_node_name`, a display
helper the Phase-1 model wrongly cited):

```python
# cognee/infrastructure/engine/utils/generate_node_id.py
def generate_node_id(node_id: str) -> UUID:
    return uuid5(NAMESPACE_OID, node_id.lower().replace(" ", "_").replace("'", ""))
```

`deduplicate_nodes_and_edges` keeps one node per `str(node.id)` (this UUID), so two
mentions merge iff their `generate_node_id` matches. The default resolver is
`RDFLibOntologyResolver(ontology_file=None, ...)`; with no ontology file the
difflib `cutoff=0.8` strategy has no candidates and never fires.

**Phase 3a:** `RealCognee` (`real_resolvers._cognee_key` / `cognee_clusters`)
reproduces `generate_node_id` VERBATIM (pure stdlib `uuid5`; GLOBAL). This **fixes
a confirmed Phase-1 bug**: the old model cited `generate_node_name` (= `name.lower()
.replace("'","")`, no `" "->"_"`) and actually used `_norm` (lower + ws-collapse) --
neither matched the real key. Reproducing the 1-line pure key + citing source is
**`validated`** (importing the heavy `cognee` package to call a uuid5 buys zero
fidelity). Observed F1 0.066.

### 4. mem0 -- `validated` (MD5 floor)

`mem0/memory/main.py`:

```python
mem_hash = hashlib.md5(text.encode()).hexdigest()              # _add_to_vector_store
new_metadata["hash"] = hashlib.md5(data.encode()).hexdigest()  # _create_memory
```

MD5 over the raw memory text, case-sensitive, no normalization, used as a hard
content-dedup fingerprint -- byte-identical to our model
`md5(r.mention.encode()).hexdigest()`. The deterministic MD5 FLOOR is
**`validated`**.

> **Out of scope (Phase 3):** mem0's real semantic merge is an LLM ADD/UPDATE
> prompt layered on top of this MD5 floor. That layer is non-deterministic and
> per-pair LLM-costed; it is NOT modeled here. This row represents only the
> deterministic dedup floor mem0 ships.

### 5. Neo4j LLM Knowledge Graph Builder -- `modeled` (partial default rule)

Real `get_duplicate_nodes` Cypher (`backend/src/graphDB_dataAccess.py`):

```cypher
MATCH (n:!Chunk&!Session&!Document&!`__Community__`)
WHERE n.embedding is not null and n.id is not null
...
WHERE elementId(n) < elementId(other) and labels(n) = labels(other)
AND (
  (size(toString(other.id)) > 2 AND toLower(toString(n.id)) CONTAINS toLower(toString(other.id))) OR
  (size(toString(n.id)) > 2 AND toLower(toString(other.id)) CONTAINS toLower(toString(n.id))) OR
  (size(toString(n.id))>5 AND apoc.text.distance(toLower(toString(n.id)), toLower(toString(other.id))) < $duplicate_text_distance) OR
  vector.similarity.cosine(other.embedding, n.embedding) > $duplicate_score_value
)
```

Constants confirmed:

```python
text_distance = get_value_from_env("DUPLICATE_TEXT_DISTANCE", 3, "int")    # 3 (README stale at 5)
score_value   = get_value_from_env("DUPLICATE_SCORE_VALUE", 0.97, "float") # 0.97
```

`apoc.text.distance` is Levenshtein. The same-label gate, the bidirectional
`CONTAINS` with the `size > 2` guard, the `Levenshtein < 3` term, and the
`cosine > 0.97` term all map to our model, and the constants are exact. But the
DEFAULT run does not faithfully reproduce the real default rule, on two counts,
so the row stays **`modeled`**:

- **(a) cosine OR-term is inactive in our default run.** The real query
  pre-filters `n.embedding is not null`, i.e. it runs over embedded nodes and
  the `cosine > 0.97` term is part of the real default (the builder embeds
  nodes at ingest). Our default run supplies no embedder, so the cosine
  OR-term is dropped and only 2 of the 3 OR-branches fire -- a **partial**
  instantiation of the real default rule. This is the SAME embedder-gating
  reason LlamaIndex stays `modeled`. Pass `--embedder` to activate it (the
  abbreviation/synonym/cross-lingual classes that dominate this corpus sit
  below a 0.97 cutoff anyway, so it barely moves them).
- **(b) edit-distance length guard is `elementId`-sided -> IRREPRODUCIBLE
  (Phase-2 verified verbatim at
  [`@4a412f46`](https://github.com/neo4j-labs/llm-graph-builder/blob/4a412f4688cf4096976045c019edc0a7f6ddcb6b/backend/src/graphDB_dataAccess.py#L417-L444)).**
  The guard is `size(toString(n.id)) > 5`, and each pair is oriented by
  `WHERE elementId(n) < elementId(other)` -- so the guard tests the
  *smaller-`elementId`* node, an arbitrary INSERTION-ORDER side unrelated to
  string length. The effective rule is therefore neither `min(len) > 5`
  (under-fires) nor `max(len) > 5` (over-fires); it is order-dependent on
  Neo4j-internal `elementId`, which no commutative pairwise predicate can
  reproduce and which the benchmark's record order cannot be guaranteed to
  match. We keep the conservative two-sided `min(len) > 5` and RECORD the
  divergence rather than trade it for an equally-wrong `max`.

The string predicates and constants ARE source-confirmed (so the row is a
well-grounded floor), but a partial + irreproducible default run is not a
faithful reproduction of the framework's default, which is what `validated`
requires. Crucially, **(b) means even the `--embedder` variant
(`Neo4j-KGBuilder(emb)`, which fixes (a) by activating the cosine OR-term) stays
`modeled`** -- the irreproducible guard is not something an embedder can close.

### 6. neo4j-graphrag fuzzy -- MODEL `modeled`, REAL `real-inproc`

`resolver.py`:

```python
return fuzz.WRatio(text_a, text_b, processor=utils.default_process) / 100.0
# similarity_threshold default 0.8 (BasePropertySimilarityResolver)
```

The MODELED adapter's per-pair predicate is byte-identical to this. **It still
diverges from a real run on this corpus:**

- `neo4j-graphrag(fuzzy)*` (`RealNeo4jGraphRAGFuzzy`, **`real-inproc`**) runs the
  library's real `compute_similarity` + `_consolidate_sets`, grouped per entity
  label, with only the Neo4j/APOC I/O stubbed. Measured **F1 0.469**.
- `neo4j-graphrag(fuzzy)` (`Neo4jGraphRAGFuzzyModeled`, **`modeled`**) measures
  **F1 0.403** (-6.6pp).

The two are kept side by side deliberately for the model-vs-real contrast. The
divergence is in the consolidation/grouping behavior, not the WRatio predicate.

> **Phase-2 `_consolidate_sets` partition fix (F1 0.470 -> 0.469).** The library's
> `_consolidate_sets` is a SINGLE PASS: a pair bridging two already-separate
> consolidated sets merges into only the first, leaving them OVERLAPPING (a record
> in two clusters). The real resolver's sequential Neo4j merges transitively
> collapse the shared record into one entity; we reproduce that disjoint end-state
> with `_merge_overlapping` (a no-op when there is no overlap). Phase-1's fuzzy row
> had 12 duplicate ids and scored F1 0.470 on that malformed partition; the
> corrected valid partition scores **0.469** (P 0.491->0.477, R 0.451->0.461). The
> +6.6pp-over-the-model finding is unchanged. spaCy's denser pair graph hit the
> overlap hard, which is how the bug surfaced.

### 7. neo4j-graphrag exact -- `validated`

`SinglePropertyExactMatchResolver` has **no callable Python decision method**;
its entire rule is the Cypher in `run()`:

```cypher
WITH entity, entity.name as prop
WITH entity, prop WHERE prop IS NOT NULL
UNWIND labels(entity) as lab
WITH lab, prop, entity WHERE NOT lab IN ['__Entity__', '__KGBuilder__']
WITH prop, lab, collect(entity) AS entities
```

i.e. exact `name` equality per label, null/missing names skipped, NO
normalization, merge via `apoc.refactor.mergeNodes`. We re-expressed that Cypher
in `real_resolvers.neo4j_graphrag_exact_clusters` and confirmed it line-by-line
against source. Because there is no real Python decision code to execute, this
is **`validated`** (a confirmed re-expression), not `real-inproc`. Measured
**F1 0.066** (P 0.875 / R 0.034) -- exact-name-per-label is high-precision and
low-recall on this hard corpus by construction.

### 8. LlamaIndex PropertyGraphIndex -- `modeled`

The model's rule -- same-label gate AND (contains OR Levenshtein<5 OR
cosine>0.9), KNN top-10 when embedded, with `TEXT_DISTANCE=5` / `COSINE=0.9` --
comes from a Neo4j/Bratanic *property-graph blog* using a hand-written Cypher
over `Neo4jPropertyGraphStore`, NOT from maintained library code. The maintained
`run-llama/llama_index` property-graph code ships **no automatic fuzzy
entity-dedup default** (no `apoc.text.distance` / `edit_distance` /
`word_edit_distance` in the property_graph modules; the library default is exact
`name`+`label` upsert at the graph store). So this model both (a) cannot pin its
constants to maintained source, and (b) likely over-states the library's default
behavior. Because the citation is a blog rather than maintainable source we can
pin a constant to, it stays **`modeled`** (conservative -- cannot confirm).

### 9. neo4j-graphrag spaCy -- `real-inproc` (Phase 2)

`SpaCySemanticMatchResolver` subclasses the same `BasePropertySimilarityResolver`
as the fuzzy resolver and exposes a callable `compute_similarity` (spaCy
doc-vector cosine over the `en_core_web_lg` model) + `_consolidate_sets`. We run
those real library methods in-process per entity-label (only Neo4j/APOC I/O
stubbed), so the tier is **`real-inproc`** -- the same posture as the fuzzy row,
NOT `validated` (the exact resolver's tier, which has no callable decision code).

We construct it with `auto_download_spacy_model=False` so a missing model RAISES
(degrading the row to "skipped" via the import-guarded registry) rather than
triggering an implicit ~560MB download; CI installs the model explicitly.

Measured **F1 0.401 (P 0.699 / R 0.281)** -- another framework default goldenmatch
`auto+fields` (0.602) beats. spaCy's semantic vectors recall org-suffix and
nickname variants well (`suffix` 0.868, `nick` 0.800) but miss cross-lingual and
typo classes entirely (`xling` 0.0, `typo` 0.0), a different profile from the
WRatio fuzzy resolver.

### 10. Graphiti -- `real-inproc` (Phase 3a, NEW row)

Graphiti's deterministic dedup floor is a clean, separable, pure-Python decision
in `graphiti_core/utils/maintenance/dedup_helpers.py`: `_resolve_with_similarity`
+ `_build_candidate_indexes` resolve each extracted node against existing nodes by
exact normalized-name (`_normalize_string_exact` = lower + ws-collapse), else a
MinHash/Jaccard `>= _FUZZY_JACCARD_THRESHOLD (0.9)` fuzzy match, with a
low-entropy / short-name gate (`_NAME_ENTROPY_THRESHOLD=1.5`, `_MIN_NAME_LENGTH=6`).
No LLM, DB, or embedder is touched by the floor (stdlib + pydantic only). It is
**label-agnostic** -- the floor applies no entity-label gate (verified at source).

`RealGraphiti` (`real_resolvers.graphiti_clusters`) runs that real code via
**sequential ingestion**: each record resolves against the accumulated existing set,
mirroring Graphiti's real extracted-vs-existing-graph flow (the real code does NO
intra-batch dedup). Honest scope recorded so a skeptic can't mistake it:

- **No LLM/embedder.** Unresolved nodes (0 exact + no fuzzy hit, >1 exact ambiguous,
  or low-entropy) become NEW entities -- the deterministic-floor end state. The full
  default path would escalate those to the LLM; this row is the FLOOR, not that path.
- **We feed the full existing set as candidates.** The real flow prunes candidates
  via an embedder semantic search first, so this is an UPPER BOUND on the floor
  (more candidates, never fewer) -- it can only help recall.
- The per-label grouping the other framework rows use is NOT applied here (the floor
  is label-agnostic); this is a harness apples-to-apples choice, not Graphiti behavior.

Tier **`real-inproc`** (the library's real resolution DECISION runs in-process;
only the embedder candidate-prune, the LLM fallback, and graph persistence are
elided). CI-only (`graphiti-core`, torch-free, import-guarded).

## Phase 2 -- embedding terms (measured, not asserted)

`--embedder st` activates the cosine OR-terms (MiniLM `all-MiniLM-L6-v2`, the
model Neo4j's builder actually uses) on the additive `(emb)` rows, run ALONGSIDE
the unchanged string-only rows so the board shows the effect side by side:

| row | string-only F1 | (emb) F1 | delta |
|---|---|---|---|
| Neo4j-KGBuilder | 0.456 | 0.471 | +1.5pp |
| LlamaIndex-PGI  | 0.221 | 0.234 | +1.3pp |

**The measured delta confirms the by-construction prediction: the embedder does
NOT rescue the classes that dominate this corpus.** The per-class F1 of the
dominant classes is BYTE-IDENTICAL with vs without the embedder -- KGBuilder
`abbreviation` 0.412=0.412, `synonym` 0.034=0.034, `cross_lingual` 0.588=0.588
(same for LlamaIndex). The small net gain comes only from `temporal_version` and
`nickname`. Abbreviations/synonyms/cross-lingual aliases sit below a 0.9/0.97
cosine cutoff by construction, so the cosine OR-term cannot generate the pairs
string blocking misses. Both `(emb)` rows stay **`modeled`** (KGBuilder for the
irreproducible `elementId` guard above; LlamaIndex for the unconfirmable
blog-sourced rule -- an embedder closes neither gap).

## Phase 3 -- mem0 LLM merge layer (measured, not asserted)

mem0's deterministic MD5 floor is the `validated` row (F1 0.066). Phase 3 asked
whether its LLM ADD/UPDATE semantic-merge layer -- the part scoped out of the floor
-- adds entity-resolution recall. MEASURED on the full corpus (keyed: real
`mem0.Memory.add()` per mention, gpt-4o-mini + in-memory qdrant; isolated venv,
OpenAI key via Infisical):

| layer | P | R | F1 | non-zero classes |
|---|---|---|---|---|
| mem0 MD5 floor (`validated`) | 0.875 | 0.034 | 0.066 | cross_document_exact |
| mem0 LLM add/merge (real) | 0.833 | 0.025 | **0.048** | cross_document_exact only |

**The LLM layer is a NET NEGATIVE for ER -- worse than its own MD5 floor.** Every
recall-bound class is 0.000 (abbreviation, synonym, cross_lingual, nickname,
org_suffix, typo, temporal, collision); only cross_document_exact survives, and at
LOWER recall than the floor. Mechanism (confirmed on controlled inputs): **mem0
resolves MEMORIES, not ENTITIES.** Its LLM fact-extraction front-end DROPS bare
non-memorable mentions ("IBM", "I.B.M." -> no fact stored) and REWRITES others into
dated first-person facts ("User mentioned International Business Machines (IBM) on
<date>"). Surface-form variants get SEPARATE `ADD` events even when the LLM
explicitly recognizes them -- "Apple Incorporated" is stored as a distinct memory
annotated "an alternative name for Apple Inc", never merged; same for Bayer
AG/Aktiengesellschaft and JPMorgan & Co./and Company. So the layer merges zero
variants AND loses some exact-duplicate recall the deterministic floor keeps.

This is the THIRD independent confirmation that this corpus's dominant classes are
recall-bound and immune to precision-side levers: the Phase-2 embedder (cosine
OR-term) was a measured no-op, the keyed `auto+llm` pair-filter couldn't create
pairs blocking never generated, and now mem0's LLM merge degrades rather than
improves. **mem0's ER ceiling is its MD5 floor (0.066); the LLM layer is NOT built
as a board row** -- it would ship a strictly-worse, token-costed, non-deterministic
number. Keyed + non-deterministic -> prose-only, OUT of the committed table (same
posture as the Phase-2 embedder finding).

Method: per-mention `add()` events mapped to clusters (ADD = new memory; empty
result = floor-merged byte-identical repeat OR dropped-at-extraction singleton).
One keyed run -- the mechanism is deterministic by design (variants always ADD
separately; controlled smoke confirmed it independent of the headline F1).

The **cost** side of this finding -- mem0's ~2 LLM calls/record buying a
strictly-worse-than-free number, against goldenmatch's deterministic ER at 0 calls /
$0 -- is tabulated in [`../COST.md`](../COST.md).

## Deferred

- **mem0 LLM ADD/UPDATE merge** -- Phase 3 PROBE DONE (measured net-negative; see the
  "Phase 3" section above). Not built as a row; the MD5 floor `validated` row stands.
- **Live-Neo4j (Phase 4)** -- convert the neo4j-graphrag `*` rows to `real-live` and
  run Neo4j-KGBuilder's real Cypher against a live store (the only honest escape from
  its `elementId`-sided guard). Lowest value; do last or never.

## Still `modeled` after Phase 3a

Phase 3a cut the exact-key family (GraphRAG, LightRAG, Cognee) and added Graphiti
over to faithful rows (see the Phase-3a banner + sections 1-3 + 10). What REMAINS
`modeled`, and why:

- **LlamaIndex PGI** -- **lack of confirmable source**: its constants live in a
  Neo4j/Bratanic blog, and maintained `run-llama/llama_index` ships no equivalent
  fuzzy-dedup default. An embedder does not close a provenance gap -> `(emb)` stays
  modeled too.
- **Neo4j-KGBuilder** -- string predicates + constants are source-confirmed, but the
  default run is PARTIAL (cosine OR-term needs an embedder) AND the edit-distance
  length guard is `elementId`-sided -> IRREPRODUCIBLE by a commutative predicate
  (Phase-2 verified). Even the `--embedder` variant stays modeled. The only honest
  escape is a live-Neo4j run of the real Cypher (Phase 4 / never).
- **neo4j-graphrag(fuzzy) MODEL** -- kept ALONGSIDE the `real-inproc` `*` row purely
  for the model-vs-real contrast (model F1 0.403 vs real 0.469, +6.6pp); it is a
  deliberate teaching row, not an unconfirmed one.

Of the original modeled framework defaults, the Phase 0-3a arc converted every one
that could be honestly run/confirmed (neo4j-graphrag fuzzy/spaCy `real-inproc`,
neo4j-graphrag exact + GraphRAG + Cognee `validated`, LightRAG + Graphiti
`real-inproc`, mem0 MD5 floor `validated`); only LlamaIndex (unconfirmable) and
Neo4j-KGBuilder (irreproducible guard) remain modeled -- which is itself the
headline finding: real built-in dedup defaults are shallow, divergent, and
sometimes irreproducible.
