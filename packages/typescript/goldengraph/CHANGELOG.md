# Changelog

All notable changes to `goldengraph` are documented here.

## [0.1.0] - 2026-06-28

Initial release: edge-safe TypeScript surface for the GoldenGraph knowledge-graph engine (v1 — the 4 graph + query ops).

### Added
- `buildGraph(mentions, edges, resolution)` — build a resolution-merged entity graph.
- `neighborhood(graph, seeds, hops)`, `seedsByName(graph, name)`, `communities(graph)`.
- Opt-in `goldengraph/wasm` subpath (`enableGoldengraphWasm()`) loading the same
  pyo3-free Rust kernel (`goldengraph-core`) the Python/C bindings use, via WebAssembly.
  The base import pulls zero wasm bytes and stays edge-safe.
- Refusing contract: query functions throw an actionable error until the backend is enabled.

### Not yet wired
- The kernel's bitemporal store (`store_append/as_of/history`) — a fast-follow.
