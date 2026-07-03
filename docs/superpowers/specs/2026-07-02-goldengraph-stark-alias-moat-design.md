# Alias-injected STaRK — the ER-moat experiment (design)

**Program:** STaRK retrieval. **Goal:** show goldengraph's entity resolution earns
its place on a REAL retrieval benchmark where vanilla STaRK cannot — by corrupting
a real STaRK KB with duplicate/alias nodes so a strong dense baseline **collapses**,
then showing **ER-native ingest recovers retrieval quality that ad-hoc dedup loses.**

## Why (the motivating negative)

PR #1402 proved that on **vanilla** STaRK-PRIME with a FAIR dense baseline (embed
each node's intrinsic `get_doc_info(add_rel=False)` doc), the naive 1-hop graph
walk does NOT earn its place: graph recall@20 0.213 vs dense 0.261 (−18%); the
earlier names-mode "+39%" was a weak-baseline artifact. Honest conclusion: vanilla
STaRK is **pre-resolved and text-rich**, so structure adds nothing over text — the
ER moat is invisible there. This experiment builds the battlefield where
**resolution is load-bearing**: alias noise fragments the text signal, dense
degrades, and only a real resolver recovers it.

## The load-bearing mechanism insight

The store (`goldengraph-core/src/store.rs`) merges entities on `record_key`
overlap: it unions `record_keys`, `surface_names`, `source_refs`, and remaps
edges — but it has **NO description/text field**. A node's rich doc
(`get_doc_info`) lives only in the adapter and is used to build the `EntityIndex`;
it never enters the store. So "resolution" acts in **two different places**:

- **Dense arm** — ER must unify the TEXT: build one `EntityIndex` embedding per
  resolved cluster over the **concatenated member docs**. Merging in the store
  alone does nothing for dense retrieval.
- **Graph arm** — ER unifies the EDGES: cluster-shared `record_keys` → the store's
  overlap-merge unions the fragmented neighborhoods so the 1-hop walk can traverse
  the whole entity.

So "ER-native ingest" is a **clustering step that feeds both** the index (merged
text) and the store (merged edges), not just a record_key relabel.

## Conditions (all on PRIME, over the full-text `get_doc_info` corpus)

|            | dense                                   | graph (secondary)         |
|------------|-----------------------------------------|---------------------------|
| **clean**  | reuse PR #1402 (recall@20 0.261)         | reuse PR #1402 (0.213)    |
| **fragmented** | each alias its own node (split text+edges) | "                     |
| **ad-hoc** | merge by normalized-exact-name           | "                         |
| **ER**     | merge by goldenmatch `dedupe_df`         | "                         |

**Moat = ER recovers toward clean while ad-hoc does not** (ad-hoc's exact-string
match cannot merge variant surface forms). Clean is already measured, so ONE new
Modal run covers fragmented / ad-hoc / ER × dense / graph (6 cells).

## Components

### 1. `stark_inject.py` — the corruption (box-TDD'd)

```python
def inject_aliases(nodes, node_texts, edges, target_ids, *, k=3, seed=0):
    """Fragment each entity in `target_ids` into `k` alias nodes.

    nodes: [(stark_id:str, name:str, typ:str)]; node_texts: aligned docs;
    edges: [(subj_id, pred, obj_id)]; target_ids: set[str] to fragment.

    For each target entity E (id, name, doc, incident edges):
      - mint k NEW alias ids (namespaced, e.g. f"{id}#a{j}"), each a deterministic
        VARIANT surface name of E's name (seeded rng: abbreviation / word-order /
        truncation / punctuation-drop -- mirrors engineered._render_mention);
      - ROUND-ROBIN E's doc SENTENCES across the k aliases (each alias doc = its
        slice -> no alias has the full text -> dense degrades);
      - ROUND-ROBIN E's incident EDGES across the k aliases (each alias holds a
        subset of the neighborhood -> the walk degrades); the endpoint that is
        itself a target is remapped to one of ITS aliases (round-robin) too, so
        fragmentation composes across an edge whose both ends are injected.
      - drop the original node E (replaced by its aliases).

    Returns (nodes', node_texts', edges', canon) where `canon: dict[str,str]`
    maps every alias id -> E's ORIGINAL id (identity for untouched nodes)."""
```

Deterministic + seeded (reproducible). Non-target nodes pass through unchanged
with `canon[id] = id`. Variant generation is a small pure helper
`_variants(name, k, rng)` returning `k` distinct surface forms (fall back to
`f"{name} ({j})"` if the name is too short to vary `k` ways, so aliases stay
distinct — ad-hoc-exact must NOT accidentally merge them, or the baseline is
rigged).

### 2. `resolve_aliases(alias_nodes, method) -> clusters` (box-TDD'd)

`alias_nodes`: `[(alias_id, name)]` for the injected set only (bounded, fast).
Returns `clusters: list[list[str]]` (alias_ids per cluster).

- `method="none"` — singletons (fragmented floor).
- `method="exact"` — group by `name.lower().strip()` (ad-hoc dedup; only exact
  duplicates merge, variant surface forms stay split).
- `method="er"` — goldenmatch `dedupe_df` over the alias names (the real resolver;
  fuzzy, merges variant surface forms). Lazy import; box-testable on a small
  variant-name fixture. Runs over the injected alias set only.

A cluster's **canonical id** = the `canon`-mapped original (all aliases in a true
cluster share one original; a resolver error that mixes two originals is a real
ER mistake and is scored as such — not hidden).

### 3. Per-condition materialization

Given `clusters` (from a method) over the injected aliases + the pass-through
non-target nodes as singletons:

- **Index (dense):** one entry per cluster, `entity_id = int(hash-free cluster
  ordinal)`, `canonical_name = " ".join(member docs)` (the merged text). Keep a
  `cluster_ordinal -> canonical original id` map for scoring. Fragmented → each
  alias is its own cluster → fragmented text embeddings.
- **Store (graph):** `bulk_load` with `record_keys = [cluster_key]` shared within
  a cluster (the store then unions the members' edges); non-target singletons keep
  their unique key (passthrough, as SP2). The graph arm walks the merged
  neighborhood.

Reuses SP2's `bulk_load` unchanged (record_keys is the only lever) and the
full-text `EntityIndex` build path.

### 4. Canonical-equivalence scoring

Both arms return retrieved ids (cluster ordinals for the index / stark ids for the
graph). Map each retrieved id → its **canonical original id** via the cluster maps
+ `canon`. A query's gold set (original stark ids) is compared against the
canon-mapped ranked list, deduped first-seen. Reuse `stark_metrics.metrics`
unchanged. So a retrieval "hits" iff it surfaces ANY node whose canonical original
is a gold entity — well-defined even though the gold entity is split into k aliases.

## Data flow

```
load_stark_kb(prime, with_text=True) -> nodes, edges, queries(gold ids), node_texts
gold_ids = union of sampled queries' gold sets
nodes2,texts2,edges2,canon = inject_aliases(nodes,node_texts,edges, gold_ids, k=3, seed)
for method in (none, exact, er):
    clusters = resolve_aliases(injected_alias_nodes, method)          # non-targets = singletons
    index    = EntityIndex.build(one entry/cluster, canonical_name=joined docs)
    store    = bulk_load(cluster-keyed nodes2, edges2)                # store unions edges per cluster
    slice    = store.as_of(BIG,BIG); stark<->eid maps
    for arm in (dense, graph):
        ranked = evaluate(...)                    # ids -> canonical originals -> metrics vs gold
    -> row(method, arm)
compare vs clean (PR #1402): does ER recover recall@20 that fragmented/adhoc lose?
```

## What proves / refutes the moat

- **Moat CONFIRMED** if, on the fragmented KB, `ER-dense recall@20` recovers
  toward the clean 0.261 while `adhoc-dense` and `fragmented-dense` stay
  depressed — i.e. `ER > adhoc` by a real margin. (Secondary: same shape on the
  graph arm.)
- **Moat REFUTED / weak** if ER ≈ adhoc (the resolver merges nothing the exact
  match doesn't) or fragmented-dense barely drops (text fragmentation didn't hurt
  — injection too weak). Either is an honest, publishable result and tells us the
  injection/knobs to change.

## Testing (box-safe, TDD)

Real `PyStore` loads on the box, so injection/resolution/materialization/scoring
are box-TDD-able; only the STaRK download + embed + Modal run is integration.

`tests/test_stark_inject.py`:
- k aliases minted per target; original dropped; non-targets pass through; `canon`
  maps aliases→original and is identity elsewhere.
- doc sentences partitioned across aliases (union = original, no alias = full).
- edges distributed across aliases; an edge with both ends injected remaps both.
- variant names are distinct (ad-hoc-exact would NOT merge them) — the anti-rigging
  guard.
- determinism: same seed → identical output.

`tests/test_resolve_aliases.py`:
- `none` → singletons; `exact` → merges only identical names; `er` → merges variant
  surface forms of one entity that `exact` leaves split (the moat in miniature),
  on a small fixture (goldenmatch dedupe available in CI; box run uses main .venv).

`tests/test_alias_scoring.py` (pure): canon-mapped ranked list → a hit when any
alias of the gold entity is retrieved; equivalence-class recall.

Box runner: `cd packages/python/goldengraph`… (goldengraph inject/score) and
`cd …/er-kg-bench` for resolve/scoring, per the SP2 shadow-PYTHONPATH pattern.

## Modal integration

Extend `scripts/distill/modal_stark.py` with an `--inject` mode (`k`, `seed`):
load PRIME full-text, inject over the sample's gold ids, run the 3 methods × 2 arms,
print a table (recall@20/hit@k/mrr per cell + the ER−adhoc margin), persist to
`stark_prime_inject.md`. Same detach+spawn+poll harness. PRIME only, one setting.

## Scope / YAGNI

- **In:** PRIME, sample 200, k=3, one injection setting, 3 methods × 2 arms + reuse
  clean; dense is the primary read, graph secondary.
- **Deferred:** background-injection rate + ambiguity-dial sweep; AMAZON/MAG;
  routing ER through the full goldengraph resolve()/profile-link pipeline (this cut
  uses goldenmatch `dedupe_df`); real-alias sources (UMLS synonyms). A smarter
  graph arm (dense-ranked/degree-capped expansion) stays out — the naive walk is
  the honest secondary read.
