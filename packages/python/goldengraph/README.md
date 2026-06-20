# goldengraph

Build an own-your-KG knowledge graph from text:

**text → LLM extraction → goldenmatch entity resolution → a durable, bi-temporal store.**

Entity resolution is the differentiator — duplicate surface forms across
documents collapse into one durable entity, the thing most GraphRAG frameworks
do badly.

```python
from goldengraph import ingest, OpenAIClient
from goldengraph_native import _native as gg

store = gg.PyStore()                 # durable bi-temporal store (SP2 engine)
llm = OpenAIClient()                 # or any object with .complete(prompt) -> str
ingest("Acme Inc, founded by ...", store, at=1, llm=llm)

snapshot = store.snapshot()          # canonical JSON; persist + reopen later
view = store.as_of(valid_t=1, tx_t=1)   # bi-temporal slice -> queryable graph
```

- **`extract(text, llm)`** — text → typed entities + relationships (LLM).
- **`resolve(mentions)`** — goldenmatch's zero-config `dedupe_df` → merged entities + `:h1:` record keys.
- **`ingest(text, store, *, at, llm)`** — the end-to-end path into the store.

Part of the [goldengraph](https://github.com/benseverndev-oss/goldenmatch) program.
The engine (`goldengraph-native`: store / query / communities) is pyo3-free Rust;
this package is the Python host pipeline. LLM access is provider-agnostic
(`LLMClient` protocol); an `OpenAIClient` ships behind the `[openai]` extra.

**Scope:** SP4b (this package) is the *build* path. Retrieval + synthesis +
NL query (SP4c) and WASM/C surfaces (SP5) are separate slices.
