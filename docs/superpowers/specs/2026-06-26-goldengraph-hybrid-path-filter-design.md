# Goldengraph hybrid synthesis — path-preserving subgraph relevance-filter

## Context

The ER-KG-bench head-to-head (evidence program #1) measured the goldengraph
**hybrid** retrieval mode (passages + name-keyed subgraph) at **0.420** mean
answer-match on the same-seed instrument (musique, gpt-4o-mini, N=50, amb=0.0),
**below** the pure passage retriever `goldenmatch_rag` at **0.520**, and below
`goldenmatch_entity_rag` at 0.460. Hybrid lags rag at every hop except a thin
4-hop tie:

| engine | mean | 2-hop | 3-hop | 4-hop |
|---|---|---|---|---|
| goldengraph hybrid | 0.420 | 0.452 | 0.385 | 0.333 |
| goldenmatch_rag | 0.520 | 0.561 | 0.523 | 0.300 |
| goldengraph graph-only (prior) | ~0.22 | — | — | — |

The hybrid path recovered most of the fidelity gap over graph-only (0.22 →
0.420 — the date/number/phrase golds the triples drop are now reachable via the
passages), but layering the **full** seeded ball on top of the gold passages did
not add multi-hop lift; if anything the off-topic neighbours the 4-hop ball drags
in **dilute** the synthesis context, so the net is below passages-alone.

This spec adds one lever: **filter the subgraph for relevance before injecting it
into hybrid synthesis**, removing the diluting off-topic content while keeping
every bridge entity of the true multi-hop chain.

### Binding constraint — the 2026-06-22 revert

A prior subgraph-narrowing attempt (a relation-aware *focusing* pass that pruned
the ball to the predicates the query named) was measured **worse** on this same
bench and reverted on 2026-06-22. The lesson is recorded in the
`_retrieve_local` docstring in `answer.py`: real LLM-extracted predicates rarely
match the query's relation words verbatim, so a **topology-blind** prune dropped
the true chain. The bridge entities of a multi-hop question are, by definition,
usually **not** named in the question and score **low** on any query-similarity
signal — so any filter that scores-and-drops by query relevance risks cutting
exactly the bridges. The filter in this spec must be **chain-safe**: it must never
drop an entity that lies on a path between two anchors.

## Goal / success criteria

Run a new bench leg at the **same seed** as the 0.420 control and the 0.520
`goldenmatch_rag` baseline, with the filter on. Decision rule:

| filtered-hybrid result | reading |
|---|---|
| **≥ 0.520, esp. closes 3-hop (0.385 → ~0.52)** | path-prune removed the dilution → graph earns its keep → productize (persist passages on the Rust node) |
| **> 0.420 but still < 0.520** | filter helped but the graph still cannot beat raw text → ship `goldenmatch_rag` standalone; hybrid stays opt-in/experimental |
| **≈ 0.420** | dilution was not the mechanism → the graph is dead weight on this corpus; stop |

## Design

### Architecture

A new pure function in `goldengraph/`:

```python
def filter_subgraph_to_paths(
    subgraph: dict, seeds: list[int], *, halo: int = 1
) -> dict
```

applied in `ask()` **only on the `hybrid` branch**, between building the seeded
ball (`_retrieve_local`) and calling `synthesize_hybrid`. `mode="local"` and
`mode="global"` are untouched, so the byte-identical local baseline guarantee is
preserved.

No Rust / store change. The subgraph is already a plain `{entities, edges}` dict
(`entities`: list of `{entity_id, canonical_name, typ, ...}`; `edges`: list of
`{subj, obj, predicate, ...}`). The filter is BFS over the edge list in Python.
This keeps the experiment wheel-free, consistent with the hybrid path's posture
(passages are injected at answer time, not persisted on the node).

### Algorithm

Edges are treated as **undirected** for path-finding, because hybrid synthesis
follows edges in either direction (the prompt says "Follow edges in EITHER
direction").

```
keep = set(seeds)
build undirected adjacency from edges (subj <-> obj)
for each unordered pair (si, sj) of distinct seeds:
    p = one shortest path si -> sj over the adjacency   # BFS
    keep |= nodes(p)                                     # {} if disconnected
for each seed s:
    keep |= neighbors(s) within `halo` hops             # halo=1 by default
entities' = [e for e in subgraph["entities"] if e["entity_id"] in keep]
edges'    = [e for e in subgraph["edges"]
             if e["subj"] in keep and e["obj"] in keep]
return {**subgraph, "entities": entities', "edges": edges'}
```

**Determinism.** BFS explores neighbours in ascending `entity_id` order and
returns the first shortest path found, so ties resolve to the lowest-id next-hop
path, stably across calls. (Equal-length shortest paths otherwise are
non-deterministic.)

**Chain-safety.** Every bridge on an anchor-to-anchor path is in `keep` by
construction. Only entities on **no** anchor-to-anchor path and outside every
seed's `halo` are dropped — i.e. the dangling off-topic leaves the 4-hop ball
dragged in. This is the property the reverted predicate-focus lacked.

### Degenerate cases

- **0 or 1 distinct seed** → no pairs → `keep` = seeds + their halo. Still prunes
  the ball's far leaves (a single anchor's answer is usually a direct neighbour,
  covered by `halo=1`).
- **No seeds at all** → no-op: return the subgraph unchanged (mirrors
  `_retrieve_local`'s `if not seeds` branch; nothing to anchor a filter on).
- **Empty subgraph** → empty out, no crash.
- **Disconnected seed pair** → contributes nothing beyond the seeds + halos; the
  filter never errors on an unreachable pair.
- The result is never empty when the input is non-empty (seeds + halo always
  survive).

### Gating

Default **OFF** behind `GOLDENGRAPH_HYBRID_FILTER`:

- `""` / `none` / unset → `ask` passes the **unfiltered** subgraph to
  `synthesize_hybrid` (the current hybrid 0.420 control stays reproducible).
- `path` → apply `filter_subgraph_to_paths` before synthesis.

`ask()` reads the env at call time (same lazy-env pattern as
`_literals_enabled()` in `synthesize.py`). Optional `halo` stays a function arg
with default 1; not surfaced as a separate env unless a leg needs it (YAGNI).

## Testing

Pure offline tests (no LLM, no embeddings, no native, no network) mirroring
`tests/test_hybrid_synthesis.py`:

1. **Keeps the chain** — synthetic subgraph with a known chain
   `s1 → b1 → b2 → s2` plus off-topic leaves hanging off `b1`; seeds `{s1, s2}`;
   assert the filter keeps `{s1, b1, b2, s2}` and drops the leaves and their
   edges.
2. **Halo** — a direct neighbour of a single seed (no second seed) is kept at
   `halo=1`, dropped at `halo=0`.
3. **Determinism** — two seeds with two equal-length shortest paths between them
   → stable lowest-id-next-hop selection across repeated calls.
4. **Degenerate** — no seeds → subgraph returned unchanged; empty subgraph →
   empty out; disconnected seed pair → no error.
5. **`ask` integration** — `mode="hybrid"` with `GOLDENGRAPH_HYBRID_FILTER=path`
   hands `synthesize_hybrid` the **filtered** subgraph; with the flag off it
   hands the **full** ball. Asserted with a fake LLM / embedder / passage
   retriever — no network. (Spy on the subgraph argument passed to synthesis.)

## Bench wiring

- Add a `GOLDENGRAPH_HYBRID_FILTER` env passthrough in the goldengraph QA-e2e
  engine adapter
  (`benchmarks/er-kg-bench/erkgbench/qa_e2e/engines/goldengraph.py`), alongside
  the existing `GOLDENGRAPH_QA_MODE` handling.
- Add a `goldengraph_hybrid_filter` workflow input to `bench-graphrag-qa.yml`,
  wired to the env, so a leg runs `qa_mode=hybrid filter=path` at the same seed
  (musique, gpt-4o-mini, N=50, amb=0.0) as the control + rag baseline.
- The graph half (build + KG extraction) is unchanged; embedding calls remain
  unbilled (parity with the other engines).

## Scope guard (YAGNI)

Out of scope: ANN index, persisted embedding sidecar, any `mode="local"` change,
multi-path keep (one shortest path per pair is enough — bridges of the answering
chain lie on *a* shortest path). If the leg wins and we productize, that is when
passage persistence on the Rust node gets built — not now.
