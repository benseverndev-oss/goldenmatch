# goldengraph: a native, multi-language, ER-first KG engine — design (SP1 + program)

**Status:** Design approved (brainstorm) 2026-06-19. SP1 awaiting plan.
**Author:** Claude (with Ben)
**Related:** ER-KG-Bench (`packages/python/goldenmatch/benchmarks/er-kg-bench/`), `goldenmatch-kg`
(decisions/0021), the suite's pyo3-free-core + multi-binding pattern (`score-core`, `graph-core`,
`sketch-core`, `fingerprint-core`; the WASM acceleration arc).

## Motivation

ER-KG-Bench established that zero-config goldenmatch beats every popular KG framework's built-in
entity resolution at the stage that compounds (`answer_accuracy ~ ER_accuracy^hops`), at $0 / zero
LLM calls. goldenmatch is the entity-resolution layer, not a KG builder. `goldenmatch-kg` (0021)
drops it into other frameworks. This project goes further: build our **own** KG, whose
differentiator is exactly where incumbents are weakest — entity accuracy and stable identity — and
make the engine **native (Rust) with bindings for Python / JS-TS / C**, consistent with the suite's
existing pyo3-free-core + multi-language direction.

**The thesis, end to end:** extraction emits relationships between *mentions* ("Apple Inc"
founded-by "Jobs"; "Apple" released "iPhone"). Resolution clusters mentions into entities and
rewrites every edge endpoint mention -> entity id. With goldenmatch resolution, "Apple Inc" and
"Apple" collapse to one node and *both* facts attach to it; with the exact-match resolution the
frameworks default to, they stay two nodes and the facts split — so a 1-hop neighborhood query finds
everything vs half. That single difference is the whole product.

## The collision we are honest about

The ER *differentiator's magic* — the zero-config **auto-config controller** — is Python (with a TS
port), NOT native. Only the ER *kernels* (scoring, blocking, clustering) are pyo3-free Rust crates.
And two pipeline stages (LLM extraction, LLM synthesis) are HTTP I/O, not compute. So "all native"
is neither achievable nor desirable as stated. The native scope is the **engine**: graph model,
typed-edge store, resolution-aware edge merge, and neighborhood retrieval, riding the existing native
ER kernels. The LLM glue and the zero-config controller stay host-side (the controller until a future
native port — see Roadmap).

## Program decomposition

This is a multi-subsystem program, not one spec. Build in order; each is its own spec -> plan ->
implementation cycle. **This document fully specifies SP1**; SP2-SP4 + the future port are roadmap.

- **SP1 — `goldengraph-core` native engine** (Rust, pyo3-free) + Python binding. The portable heart:
  graph model + typed edges + dual-path resolution + 1-2 hop retrieval. Pure compute (no LLM).
  *This spec.*
- **SP2 — host LLM pipeline** (Python `goldengraph` package): extraction (text -> triples) +
  synthesis (subgraph -> answer) wrapping the core, reusing goldenmatch's `BudgetTracker`; embedding-
  seeded retrieval (host-side). Makes it a demoable end-to-end own-KG.
- **SP3 — TS/WASM + C bindings** of the core (the multi-language payoff) + a TS host pipeline,
  parity-gated by the SP1 golden vectors.
- **SP4 — the proof**: a controlled A/B eval (resolution-isolated: exact vs goldenmatch, whole
  pipeline otherwise identical) + a framework head-to-head demo (vanilla LlamaIndex PGI vs ours; the
  `goldenmatch-kg` LlamaIndex adapter can supply a midpoint) + a curated QA corpus where entity
  fragmentation breaks answers.

## SP1 — the native engine

### Crate + components

`packages/rust/extensions/goldengraph-core/` (next to the other core crates), pyo3-free, Cargo-
workspace deps on `score-core` / `sketch-core` / `graph-core`.

```
goldengraph-core/
  model.rs     MentionGraph (mention nodes + mention edges, pre-resolution)
               Graph        (entity nodes + typed edges, post-resolution)
               Subgraph     (a retrieved neighborhood)
  resolve.rs   ResolutionMode::Native(NativeConfig{ fields, threshold })   // wires score-core + sketch-core + graph-core
               ResolutionMode::Provided(map MentionId -> EntityId)         // host supplied (Python rode full zero-config goldenmatch)
               apply_resolution(MentionGraph, resolution) -> Graph          // rewrite every edge endpoint mention->entity id, dedup edges
  retrieve.rs  neighborhood(Graph, seed_entity_ids, hops in {1,2}) -> Subgraph   // BFS over typed edges
  lib.rs       build_graph(mentions, edges, ResolutionMode) -> Graph
               Graph::query(seeds, hops) -> Subgraph
               Graph::seeds_by_name(&str) -> Vec<EntityId>                  // exact/substring; embedding seeds are host-side
  pyo3 binding (Python first); crate stays pyo3-free so WASM/napi/C follow in SP3
```

### Data model

- **Input** (the extraction output, host-supplied in SP1): `mentions: [(MentionId, name, type)]` and
  `edges: [(subj: MentionId, predicate: String, obj: MentionId, source_ref)]`. `source_ref` is an
  opaque provenance handle (e.g. chunk id) carried through for later display.
- **Entity node** (post-resolution): `entity_id`, `canonical_name` (longest member name, tie -> lowest
  mention index, matching goldenmatch-kg's rule), `type`, member mention ids.
- **Typed edge** (post-resolution): `(subj_entity_id, predicate, obj_entity_id, [source_refs])`.
  Edges identical after endpoint rewrite are deduped, accumulating their `source_refs`.
- **Subgraph**: the seed entities + the entities/edges within `hops`.

### Dual-path resolution (the core deliverable)

`apply_resolution` is `build_graph`'s heart and is identical regardless of path: it maps each
mention to an entity id, groups mentions into entity nodes, and rewrites + dedups edges into entity
space. The two paths only differ in *who produces the mention->entity-id map*:

- **Native:** `NativeResolver` builds the map from the mentions using `sketch-core` (blocking) +
  `score-core` (pairwise scoring on the configured fields) + `graph-core` (WCC over the scored
  pairs), with an **explicit** `NativeConfig` (which fields, threshold). It is kernel *reuse*, so
  `native(config C)` == Python goldenmatch with the same explicit config C — no new ER parity surface.
- **Provided:** the host passes a `MentionId -> EntityId` map it computed (Python ran full zero-config
  goldenmatch and resolved the mentions). The engine validates the map covers the mentions, then
  applies it. Trivially deterministic.

The two paths are NOT expected to agree with each other (explicit-config vs zero-config are different
resolvers). The `Provided` path exists precisely so a host can supply zero-config quality the native
path cannot match until the controller is ported (Roadmap).

### Retrieval

`neighborhood(Graph, seeds, hops)` is a BFS over typed edges from the seed entity ids, collecting
entities and edges within `hops` ∈ {1, 2}. Self-loops and cycles handled. `seeds_by_name` does
exact/substring name matching for convenience; embedding-seeded retrieval (the realistic path) is
host-side in SP2 because it needs an embedding model.

### Public API (Rust; pyo3 mirrors 1:1)

```rust
build_graph(mentions, edges, resolution: ResolutionMode) -> Graph
Graph::query(seed_entity_ids: &[EntityId], hops: u8) -> Subgraph
Graph::seeds_by_name(name: &str) -> Vec<EntityId>
```

### Determinism + parity

- Stable id-ordering of entities, edges, and subgraph members everywhere (sort by id), so output is
  byte-deterministic and identical across bindings.
- **Golden vectors:** `fixtures/goldengraph_golden.json` pins `(mentions, edges, resolution, queries)
  -> (resolved graph, subgraphs)`. Rust-authoritative; checked by Rust now and by WASM/C in SP3 —
  same pattern as `sketch_golden.json`.
- The native resolver inherits `score-core`/`graph-core`'s existing Python<->native parity (it is
  those kernels), so no new ER parity gate is needed; a wiring test asserts `native(config C)`
  clusters match goldenmatch's kernels under config C (cross-checked against Python in CI).

### Testing

- Rust unit tests per unit (model build + edge dedup; resolve wiring + `apply_resolution`; retrieve
  BFS with self-loops/cycles).
- **Differentiator-as-a-test** (REQUIRED, named fixture `fixtures/resolution_split_merge.json`): two
  mentions of one entity carrying distinct facts; resolve `exact` (two nodes, split) vs goldenmatch
  (one node, merged); assert a 1-hop query returns all facts under resolution and only half under
  exact. This is SP1's headline integration test — not optional.
- Native-resolver kernel-reuse/wiring test (vs Python goldenmatch with the same explicit config, CI).
- Python-binding tests driving the pyo3 surface (the `Provided` path takes a plain dict — no
  goldenmatch import needed to test the engine).

### CI + placement

- A `goldengraph-core` rust lane: `cargo test` + clippy; build the pyo3 binding + run the Python
  tests. Deps are pure Rust (`score-core`/`graph-core`/`sketch-core`, no `ort`), so it links cleanly
  locally (unlike the embed crates).
- The published Python `goldengraph` *package* (the LLM wrapper) is SP2; SP1 ships the crate + its
  Python handle + the CI lane.

### Explicit non-goals (SP1)

- No LLM extraction/synthesis (SP2). No embedding-seeded retrieval in the core (SP2; name/id seeds
  only). No persistence (in-memory `Graph`; identity-graph durability is a later seam). No TS/WASM/C
  bindings (SP3, but unblocked by the pyo3-free crate). No native zero-config controller (future
  port). No community/global GraphRAG retrieval (1-2 hop only). No incremental graph mutation
  (build-once).

### Edge cases / error handling

- Empty/missing mention names; edges referencing unknown mention ids -> skip + count, never panic.
- `Provided` map missing a mention -> validation error surfaced to the host.
- Determinism preserved under duplicate edges, self-loops, and cycles.

## Risks / open questions (resolve in the plan)

- **`score-core`/`graph-core`/`sketch-core` pure-Rust API surface** — confirm each exposes the
  Rust-callable functions the native resolver needs (they are pyo3-free, so they should; pin the exact
  entry points in the plan).
- **`NativeConfig` shape** — the minimal explicit config that wires the kernels; keep it small. The
  MVP needs only one scorer + one blocking key + a threshold (resist adding the auto-config knobs —
  those are the future native-controller port, not this).
- **Mention/entity id types** — `MentionId`/`EntityId` representation (ints for compactness vs strings
  for host friendliness) across the pyo3 boundary; pick one and pin it.
- **pyo3 glue location AND packaging** — keep `goldengraph-core` pyo3-free; decide in the plan where
  the pyo3 wrapper lives: a SEPARATE wrapper crate (mirror `goldenmatch-native`/`goldencheck-native`)
  vs a feature-gated `pyo3` module inside the crate (mirror `score-core`'s WASM-via-feature). Do NOT
  let pyo3 contaminate the pyo3-free core (it must stay clean for the SP3 WASM/C bindings). Also pick
  maturin/abi3 wheel vs an in-tree `_native` module.
- **`Graph`/`Subgraph` serialization across the pyo3 boundary** — plain dicts vs dataclasses vs named
  tuples on the Python side; pin early in the plan so the Python-binding tests don't thrash.

## Roadmap (beyond SP1)

> **SUPERSEDED (2026-06-20):** the phase numbering below (SP2 = LLM pipeline, SP3 = bindings, SP4 = eval) is superseded by `2026-06-20-goldengraph-program-roadmap.md`, which inserts a native-store keystone phase and re-sequences everything foundation-first. Read that roadmap for the current plan; the items below are retained for history.

- SP2 host LLM pipeline (Python) — demoable end-to-end own-KG.
- SP3 TS/WASM + C bindings + TS host pipeline.
- SP4 controlled A/B eval + framework head-to-head demo + QA corpus.
- **Future:** port the zero-config auto-config controller to native, so the native resolver is
  zero-config and the host-ids path becomes optional (Ben, explicitly "eventually, not today").

## Decisions log (brainstorm)

1. Deliverable ambition: **do it all** — reusable library + demo + measured eval (as a program).
2. Baseline: **both** — controlled resolution-isolated A/B (eval) + framework head-to-head (demo).
3. Home: a **new `goldengraph` package** (product seed); the native core is `goldengraph-core`.
4. Native scope: the **engine** (graph + edges + retrieval + dual-path resolution); LLM glue +
   zero-config controller stay host-side.
5. Resolution: **both** — native explicit-config resolver (kernel reuse) AND accept host-resolved ids.
6. Native zero-config controller: a **future** port, not in scope now.
