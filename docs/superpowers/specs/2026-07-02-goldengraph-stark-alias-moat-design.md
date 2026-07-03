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
- **Graph arm** — ER unifies the EDGES **caller-side, in Python**: collapse the
  alias graph to ONE node per resolved cluster and remap every edge endpoint to its
  cluster's canonical id, then `bulk_load` the **pre-merged** graph. The store never
  sees the aliases.

  > **Why not let the store merge via shared record_keys:** `store.rs::append`
  > reconciles a batch only against ALREADY-STORED entities (`key_to_stored` is
  > built from `self.entities`), never against siblings in the same batch. `bulk_load`
  > issues all nodes in one batch, so N aliases sharing a key would each MINT a
  > separate id — no union — and would also leave multiple current entities sharing
  > one key (violating the store's one-key-one-entity invariant). And `bulk_load`
  > hardcodes `record_keys=[stark_id]` (no shared-key lever). So the store-merge path
  > does not work here; the Python collapse is the correct, simpler mechanism and
  > needs no `bulk.py` change (each collapsed cluster-node keeps a unique key —
  > exactly SP2's passthrough usage).

So "ER-native ingest" is a **clustering step that feeds both materializations**: the
index (one embedding per cluster over merged text) and the store (one node per
cluster over merged edges). The clustering is the single ER lever; the store's
overlap-merge is NOT used.

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

Both materializations are driven by the SAME `clusters`. Assign each cluster a
`cluster_ordinal` (int) and pick its `canonical_id` = the `canon`-mapped original
(all aliases in a true cluster share one original; a resolver error mixing two
originals picks one deterministically and is scored as the real ER mistake it is).

- **Index (dense):** one entry per cluster — `entity_id = cluster_ordinal`,
  `canonical_name = " ".join(member docs)` (the merged text). Keep
  `cluster_ordinal -> canonical_id` for scoring. Fragmented → each alias its own
  cluster → fragmented text embeddings.
- **Store (graph):** collapse to ONE node per cluster in Python — node id =
  `cluster_ordinal`, and remap EVERY edge's endpoints from alias id → the cluster
  each alias belongs to (`alias_id -> cluster_ordinal`), dropping intra-cluster
  self-loops. Then `bulk_load(collapsed_nodes, collapsed_edges)` with the normal
  unique per-node key (passthrough). The store thus holds the PRE-MERGED graph; the
  1-hop walk traverses the unioned neighborhood with no store-side merge. Fragmented
  → singleton clusters → the full un-collapsed alias graph.

Reuses SP2's `bulk_load` **unchanged** (each collapsed node keeps a unique key) and
the full-text `EntityIndex` build path. The only new code is the Python clustering
+ the two materializations built from it.

### 4. Canonical-equivalence scoring

Both arms now operate in the SAME id space — `cluster_ordinal` (index `entity_id`
= ordinal; store node id = ordinal; the graph slice's `source_refs` carry the
ordinal for the stark↔slice-eid maps, exactly as SP2 carried stark_id). Map each
retrieved `cluster_ordinal` → its **canonical original id** via
`cluster_ordinal -> canonical_id`. A query's gold set (original stark ids) is
compared against the canon-mapped ranked list, deduped first-seen. Reuse
`stark_metrics.metrics` unchanged. So a retrieval "hits" iff it surfaces ANY node
whose canonical original is a gold entity — well-defined even though the gold
entity is split into k aliases. (For the CLEAN reference reuse, ordinal = stark id
and the map is identity, so the numbers reconcile with PR #1402.)

## Data flow

```
load_stark_kb(prime, with_text=True) -> nodes, edges, queries(gold ids), node_texts
gold_ids = union of sampled queries' gold sets
nodes2,texts2,edges2,canon = inject_aliases(nodes,node_texts,edges, gold_ids, k=3, seed)
for method in (none, exact, er):
    clusters = resolve_aliases(injected_alias_nodes, method)          # non-targets = singletons
    ordinal_of = {alias_id -> cluster_ordinal};  ord2canon = {ordinal -> canonical_id}
    index    = EntityIndex.build(one entry/cluster, entity_id=ordinal, canonical_name=joined docs)
    coll_nodes = [one node per cluster, id=ordinal]
    coll_edges = [(ordinal_of[s], pred, ordinal_of[o]) for (s,pred,o) in edges2 if s!=o cluster]
    store    = bulk_load(coll_nodes, coll_edges)                      # PRE-MERGED graph (Python collapse)
    slice    = store.as_of(BIG,BIG); ordinal<->eid maps
    for arm in (dense, graph):
        ranked = evaluate(...) -> [ord2canon[o] for o in retrieved]   # -> metrics vs gold
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

`tests/test_alias_materialize.py` (real `PyStore`): the Python collapse builds one
store node per cluster with edges remapped to `cluster_ordinal`; after `bulk_load`,
`store.as_of(BIG,BIG).query([cluster_ordinal],1)` shows the UNIONED neighborhood
(edges from all the cluster's aliases), and singleton/fragmented clusters keep the
un-collapsed graph. Intra-cluster self-loops are dropped. This is the graph-arm
edge-union check that the store's own merge can NOT provide in one batch.

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
