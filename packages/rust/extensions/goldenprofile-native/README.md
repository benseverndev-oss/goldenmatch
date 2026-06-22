# goldenprofile-native

PyO3 binding for [`goldenprofile-core`](../goldenprofile-core) — the
**Virtual Fingerprint / Semantic Signature engine**.

Cross-document entity resolution over rigid, LLM-synthesized profiles
(`name | category | anchor | attribute`) for graph nodes **and** edges. Built to
repair the multi-hop knowledge-graph "shatter": disjoint neighborhoods reunite
into one entity (the defining attribute can only add confidence, never veto a
merge) while distinct entities stay apart (a hard name + category gate).

```python
import json
from goldenprofile_native import resolve_json

req = {
    "profiles": [
        {"kind": "node", "name": "Thomas Nabbes", "category": "Playwright",
         "anchor": "17th Century England", "attribute": "Wrote Play X"},
        {"kind": "node", "name": "Nabbes", "category": "Playwright",
         "anchor": "UNKNOWN", "attribute": "Born 1605"},
    ],
    # "embeddings": [[...], [...]],   # optional: one per profile, for semantic blocking
    # "config": {"scoring": {"merge_threshold": 0.72}},  # optional partial override
}
res = json.loads(resolve_json(json.dumps(req)))
res["clusters"]  # -> [[0, 1]]   the two Nabbes mentions reunited
```

The compute is in the pyo3-free `goldenprofile-core` crate; this is a thin
JSON-boundary marshaling layer, so Python, WASM, and C surfaces produce
byte-identical clusters by construction. Part of the
[goldengraph](https://github.com/benseverndev-oss/goldenmatch) program.
