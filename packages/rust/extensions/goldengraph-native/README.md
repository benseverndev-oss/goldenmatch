# goldengraph-native

PyO3 binding for [`goldengraph-core`](../goldengraph-core) -- the pyo3-free
knowledge-graph engine. Turns extracted mentions + relationships into a
resolution-merged entity graph, then answers 1-2 hop neighborhood queries.

```python
from goldengraph_native import _native as gg

mentions = [("Apple Inc", "org"), ("Apple", "org"), ("Jobs", "person"), ("iPhone", "product")]
edges = [(0, "founded_by", 2, "c1"), (1, "released", 3, "c2")]

# Native explicit-config resolution: jaro_winkler (scorer_id 0) at threshold 0.85
g = gg.build_graph(mentions, edges, ("native", 0, 0.85))
seeds = g.seeds_by_name("Apple Inc")        # -> [0] (Apple Inc + Apple merged)
view = g.query(seeds, 1)                     # -> {"entities": [...], "edges": [...]}
```

`build_graph(mentions, edges, resolution)` accepts either a `dict[int, int]`
(`mention -> entity-id`, the Provided path) or a `("native", scorer_id,
threshold)` tuple (the native resolver, reusing the score-core + graph-core
kernels). The compute is shared with the TS/WASM ([`goldengraph-wasm`](../goldengraph-wasm))
and C ([`goldengraph-cabi`](../goldengraph-cabi)) bindings via the core crate;
this wheel is a thin marshaling layer.

## Cross-surface JSON boundary

Alongside the ergonomic `PyGraph`/`PyStore` pyclasses above, the module exposes
7 **JSON-boundary** functions that mirror the `goldengraph-wasm` `*_impl`
EXACTLY (`(json, args...) -> json`, same `serde_json` over the same core):
`build_graph_json`, `neighborhood_json`, `seeds_by_name_json`,
`communities_json`, `store_append_json`, `store_as_of_json`,
`store_history_json`. Because every surface marshals the SAME core over the SAME
boundary, Python native output is **byte-identical** to the wasm / C-ABI output
by construction. These are the gate-able symbols the Python
`goldengraph.core._native_loader` probes, and the `goldengraph_native`
cross-surface parity lane (in `ci-required`) asserts them against the shared
`queries.json` oracle — the same fixture the TS
`goldengraph-wasm.parity.test.ts` uses.

Part of the [GoldenMatch](https://github.com/benseverndev-oss/goldenmatch)
extensions. No LLM, no embeddings, no persistence (those are later phases).
