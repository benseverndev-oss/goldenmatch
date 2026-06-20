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
kernels). The compute is shared with future TS/WASM/C bindings via the core
crate; this wheel is a thin marshaling layer.

Part of the [GoldenMatch](https://github.com/benseverndev-oss/goldenmatch)
extensions. No LLM, no embeddings, no persistence (those are later phases).
