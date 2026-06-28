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

> **Scope:** v1 surfaces the 4 graph + query ops. The kernel's bitemporal store (`store_append/as_of/history`) is not yet wired into this package.

## Regenerating the wasm artifact

The committed `src/core/_wasm/*` is built from the Rust kernel by `scripts/build_goldengraph_wasm.mjs` (needs `wasm-pack` + the `wasm32-unknown-unknown` target). Re-run it whenever the kernel changes; CI guards the committed artifact against drift.

## License

MIT
