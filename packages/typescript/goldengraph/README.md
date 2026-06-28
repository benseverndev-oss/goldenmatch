# goldengraph

Edge-safe TypeScript surface for the **GoldenGraph knowledge-graph engine**. Build a resolution-merged entity graph from mentions + relationships, then query it: neighborhoods, seeds-by-name, and community partition.

The engine is the **same pyo3-free Rust kernel** (`goldengraph-core`) used by the Python (`goldengraph_native`) and C bindings, surfaced here through **opt-in WebAssembly** — byte-identical results across surfaces, one kernel, no parallel reimplementation.

## Design: pure-by-default, kernel-on-opt-in

The base `goldengraph` import is pure types + the query API + a registry — **zero wasm bytes**, edge-safe (browsers, Workers, edge; no `node:*`). The kernel lives behind the `goldengraph/wasm` subpath. Until you enable it, the query functions throw an actionable error (the analog of Python requiring its native wheel).

```ts
import { buildGraph, neighborhood, communities, seedsByName } from "goldengraph";
import { enableGoldengraphWasm } from "goldengraph/wasm";

enableGoldengraphWasm();

const mentions = [
  { name: "Apple Inc", typ: "Company" },
  { name: "Apple", typ: "Company" },
  { name: "Tim Cook", typ: "Person" },
];
const edges = [{ subj: 2, predicate: "ceo_of", obj: 0, source_ref: "doc1" }];

// resolution: a { mentionIndex: entityId } map, or ["native", scorerId, threshold]
const graph = buildGraph(mentions, edges, { 0: 0, 1: 0, 2: 1 });

graph.entities;               // 2 resolved entities (Apple Inc + Apple merged)
seedsByName(graph, "Apple");  // [0] — findable by any surface name
neighborhood(graph, [0], 1);  // 1-hop subgraph around entity 0
communities(graph);           // [{ id: 0, members: [0, 1] }]
```

## API

- `buildGraph(mentions, edges, resolution): Graph`
- `neighborhood(graph, seeds, hops): Graph`
- `seedsByName(graph, name): number[]`
- `communities(graph): Community[]`
- `enableGoldengraphWasm()` (from `goldengraph/wasm`), plus `isGoldengraphWasmEnabled()` / `disableGoldengraphWasm()`.

The query functions throw until the wasm backend is enabled.

## Composing with goldenprofile

GoldenProfile resolves mentions into a cluster partition; GoldenGraph builds a
graph from a `{ mentionIndex: entityId }` resolution. The zero-dep bridge
helpers (`resolutionFromClusters`, `mentionsFromProfiles`) pipeline the two —
`goldengraph` does not depend on `goldenprofile`; you bring both:

```ts
import { resolveProfiles } from "goldenprofile";
import { enableGoldenprofileWasm } from "goldenprofile/wasm";
import { buildGraph, resolutionFromClusters, mentionsFromProfiles } from "goldengraph";
import { enableGoldengraphWasm } from "goldengraph/wasm";

enableGoldenprofileWasm();
enableGoldengraphWasm();

const { clusters } = resolveProfiles({ profiles });
const graph = buildGraph(
  mentionsFromProfiles(profiles),     // same order you resolved
  edges,                              // your mention-level relationships
  resolutionFromClusters(clusters),   // the bridge
);
```

The conversion is exact and order-preserving (profile index `i` == mention index
`i`), as long as you build the mentions from the SAME profile list you resolved.

## Bitemporal store

Ingest entities + edges over time, then read the graph "as of" a (valid-time,
transaction-time) point, or an entity's merge/split history. The snapshot is a
portable JSON value — the store ops are stateless over it (no handles cross the
boundary), so you hold and pass it back.

```ts
import { appendBatch, asOf, history } from "goldengraph";
import { enableGoldengraphWasm } from "goldengraph/wasm";

enableGoldengraphWasm();

let snap = appendBatch(null, batch1);   // null opens a fresh store
snap = appendBatch(snap, batch2);       // entities sharing a record_key merge

const graph = asOf(snap, validTime, txTime);  // bitemporal slice
const events = history(snap, entityId);        // [{ Merge: {...} } | { Split: {...} }]
```

`record_keys` (e.g. `:h1:` fingerprints) match entities across batches; a match
merges them and records a `Merge` history event.

## Regenerating the wasm artifact

The committed `src/core/_wasm/*` is built from the Rust kernel by `scripts/build_goldengraph_wasm.mjs` (needs `wasm-pack` + the `wasm32-unknown-unknown` target). Re-run it whenever the kernel changes; CI guards the committed artifact against drift.

## License

MIT
