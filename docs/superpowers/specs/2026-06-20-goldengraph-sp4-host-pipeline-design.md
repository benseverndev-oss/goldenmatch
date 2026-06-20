# goldengraph SP4 — host LLM pipeline (Python) — design

**Status:** Design draft 2026-06-20. SP4 of the program roadmap (`2026-06-20-goldengraph-program-roadmap.md`) — the standalone milestone. Decomposed into slices (SP4 is too large for one PR); **this doc fully specs SP4a** (the binding) and outlines SP4b/SP4c.

**Builds on:** SP1 (engine), SP2 (store), SP3 (communities). **Surface:** the pyo3 binding (`goldengraph-native`) + a new Python `goldengraph` package.

---

## Motivation

SP1–SP3 are a portable Rust engine (resolve → store → query → communities) with a *thin* SP1 pyo3 binding (`build_graph`/`query`/`seeds_by_name`). SP4 makes goldengraph a **standalone own-your-KG tool**: feed it text, get back a queryable, durable, bi-temporal knowledge graph with LLM-extracted entities/relationships and LLM-synthesized answers — entity resolution as the differentiator. This is the phase that lets goldengraph stand alone rather than only as a drop-in ER stage.

## Why decompose

SP4 spans six concerns that don't belong in one PR: (1) expose the store + communities to Python, (2) LLM extraction, (3) resolution + store-write, (4) embedding-seeded retrieval, (5) LLM synthesis, (6) NL-query + text-to-Cypher. (1) is LLM-free Rust+pyo3 (CI-validatable now); (2)–(6) are LLM-dependent Python needing mocked-LLM tests + API-key lanes. Slices:

- **SP4a — binding extension** (this spec, full): expose SP2's `GraphStore` + SP3's `communities` through `goldengraph-native`, so Python can build/persist/query a durable graph. No LLM. The unblocker for everything below.
- **SP4b — extraction → resolve → store pipeline** (own spec): text → triples (LLM) → zero-config resolution (reuse goldenmatch's Python auto-config controller) → `record_key` assignment (reuse `fingerprint-core` via goldenmatch) → `StoreBatch.append`. The "build a KG from text" path.
- **SP4c — retrieval + synthesis + query** (own spec): embedding-seeded retrieval (reuse `goldenembed-rs`), local + global (community map-reduce) answering (LLM synthesis), NL query over the store + a text-to-Cypher export adapter. The "ask the KG" path.

Each is its own spec → plan → implement → PR. The Python `goldengraph` package is created in SP4b (SP4a only touches the Rust binding + its Python test surface).

---

## SP4a — binding extension (fully specified)

**Goal:** the Python surface for SP2 store + SP3 communities, so SP4b/SP4c (and any host) can persist + query a durable bi-temporal graph from Python.

**Files:** `packages/rust/extensions/goldengraph-native/src/lib.rs` (extend), `tests/test_goldengraph.py` (extend). The `goldengraph` lane already builds + pytests this.

**Dependency note:** the `PyStore` surface (store) depends only on SP2 and is **implementable now** (SP2 store is on main). `PyGraph.communities()` calls `goldengraph_core::community::communities`, which lands on main only when **SP3 (#1135) merges** — so implement `PyStore` first and add `communities()` once SP3 is on main (refetch before starting).

### New surface

- **`PyGraph.communities() -> list[dict]`** — `[{ "id": int, "members": [int] }]` via `goldengraph_core::community::communities` over the wrapped `Graph`. (A "global" query = `communities()` then `query(members, hops)` per community.)
- **`PyStore`** (`#[pyclass]` wrapping `goldengraph_core::store::GraphStore`):
  - `PyStore(snapshot: str | None = None)` — constructor; `open(snapshot)` mapping `StoreError` → `ValueError`.
  - `append(batch_json: str)` — `batch_json` is a `StoreBatch` serialized to JSON (entities with `record_keys`, edges with `valid_from`/`valid_to`/`source_refs`, `ingested_at`). Parsed via `serde_json` (the SP2 types already derive `Deserialize`). **JSON-string boundary** chosen over positional tuples: it reuses the serde model, is robust to field growth, and matches the snapshot theme; the SP4b Python layer builds the JSON from typed args.
  - `as_of(valid_t: int, tx_t: int) -> PyGraph` — returns a `PyGraph` (not a dict) so the temporal slice composes with `query`/`seeds_by_name`/`communities`.
  - `snapshot() -> str`, `history(id: int) -> list[dict]`.

### Marshaling

Reuse the existing `graph_view_to_dict`. `append` does `serde_json::from_str::<StoreBatch>(batch_json).map_err(PyValueError)`. `history` maps `HistoryEvent` → `{"kind":"merge"|"split", ...}` dicts. Keep conversions explicit + small (mirrors SP1's binding style).

### Tests (in `test_goldengraph.py`, run by the `goldengraph` CI lane)

- **Store round-trip:** `append` two batches (JSON) → `snapshot()` → reopen → snapshot byte-identical.
- **Bi-temporal `as_of`:** append + correction → `as_of(v, before)` vs `as_of(v, after)` differ (reusing the SP2 scenario through Python); the returned `PyGraph.query(...)` works.
- **Merge time-travel:** `as_of(_, before_merge).query(...)` shows entities separate; after, merged (the SP2 headline through Python).
- **Communities:** build the SP1 differentiator graph → `PyGraph.communities()` groups the connected entities; isolated entity is its own.
- **Error path:** malformed `batch_json` → `ValueError`; malformed constructor `snapshot` → `ValueError` (both are `serde_json::from_str` sites).

### SP4a non-goals
No LLM, no Python `goldengraph` package yet (SP4b), no embedding (SP4c), no compact binary. Pure binding + tests.

---

## SP4b — extraction → resolve → store (outline; own spec later)

- New Python package `packages/python/goldengraph/` (standalone, **excluded from the uv workspace** like `goldenmatch-kg` — its LLM/embedding extras are heavy/fast-moving; its own CI lane). Depends on `goldenmatch` (controller, fingerprint, LLM client, `BudgetTracker`) + `goldengraph-native` (the engine).
- `extract(text, llm) -> (mentions, relationships)` — LLM prompt → typed triples. Mocked-LLM deterministic stub for tests (à la goldenmatch-kg conftest); real-LLM = opt-in lane.
- `resolve(mentions) -> (resolved entities, record_keys)` — **reuse goldenmatch's zero-config auto-config controller** (the moat, Python side) + `fingerprint-core` `:h1:` keys.
- `ingest(text, store, at)` — extract → resolve → build `StoreBatch` JSON → `store.append`. The end-to-end "KG from text."
- **Key decisions for its spec:** extraction prompt/output schema; how relationships map to `BatchEdge` valid-times (default `valid_from = ingest time`?); controller config caching across batches.

## SP4c — retrieval + synthesis + query (outline; own spec later)

- **Embedding-seeded retrieval:** `goldenembed-rs` embeds the query + entity canonical names; nearest entities seed `as_of(...).query(seeds, hops)`. **Decision for its spec:** where entity embeddings live (recompute per query vs a sidecar index; the SP2 store deliberately doesn't hold vectors).
- **Synthesis:** local (subgraph → answer) + global (per-community summary → map-reduce, using SP3 `communities`) — LLM, reusing `BudgetTracker`.
- **Query surface:** NL query over the store + a **text-to-Cypher export adapter** (thin LLM adapter for Neo4j users; the native-store NL query is primary).

---

## Cross-slice non-goals (SP4)

WASM/C bindings of any of this (SP5). The head-to-head eval (SP6). Compact binary store format. Persisting communities/embeddings in the store (community persistence is the SP2+SP3 follow-up; embedding storage is an SP4c decision). Native zero-config controller (Future — SP4 reuses the Python one).

## Risks / open questions

- **SP4a `append` JSON boundary:** ergonomic enough for SP4b (which builds the JSON) but a raw Python caller passes a JSON string. Acceptable — the SP4b Python layer is the real user-facing API; the binding is plumbing.
- **SP4b/SP4c are LLM-bound:** correctness tests must use a deterministic mocked LLM; real-LLM behavior is an opt-in lane (needs `OPENAI_API_KEY`). Accuracy is covered by SP6's eval, not SP4 unit tests.
- **Package weight:** `goldengraph` Python pulls goldenmatch + native + (optionally) embed/LLM extras — keep it out of the uv workspace (the `goldenmatch[native]` footgun) with its own lane, mirroring `goldenmatch-kg`.
