# goldengraph program roadmap (SP2 onward) — design

**Status:** Roadmap approved (brainstorm) 2026-06-20. Supersedes the "Roadmap (beyond SP1)" section of `2026-06-19-goldengraph-native-kg-engine-design.md`.

**Related:** `2026-06-19-goldengraph-native-kg-engine-design.md` (SP1, shipped via PR #1131), ER-KG-Bench (`packages/python/goldenmatch/benchmarks/er-kg-bench/`), `goldenmatch-kg` (ADR 0021), goldenmatch Identity Graph v2.

---

## Ambition (the north star this roadmap serves)

goldengraph becomes a **full standalone GraphRAG engine, head-to-head** with LightRAG / Microsoft GraphRAG / Graphiti — with **entity resolution as the differentiator**. ("Standalone" is reached in stages: local mode at SP4, global search at SP3+SP4 — see SP4.) SP1 shipped the portable in-memory heart (graph model + typed edges + dual-path resolution + 1-2 hop retrieval). This roadmap closes the six capability gaps that separate "a sharp ER kernel" from "a KG engine a developer reaches for by default":

1. Persistence
2. Temporal model
3. Community detection / global search
4. LLM extraction
5. Embedding-seeded retrieval
6. text-to-Cypher / NL query

All six are first-class. The competitive thesis (validated 2026-06-20 against current sources): the popular frameworks are weakest at entity resolution — exact-match or LLM-only dedup, no deterministic fuzzy resolution — which is exactly goldengraph's moat.

**Config posture (the SP1 collision, carried forward honestly).** The moat is *deterministic fuzzy resolution* — native, no LLM (jaro_winkler + WCC). That is present from SP1. It is NOT the same as *zero-config*: SP1's native resolver is explicit-config (the caller passes scorer + threshold, or supplies ids via the `Provided` path). Zero-config quality comes from goldenmatch's auto-config controller, which is Python (with a TS port) and NOT native. This roadmap resolves the collision by surface: SP4 (a Python package) reaches zero-config by **reusing the existing Python controller**; the WASM/C surfaces (SP5) stay explicit-config until the native controller port (Future) lands. So "standalone" does not mean "controller-free" — it means the Python `goldengraph` package owns the full pipeline end-to-end.

## The keystone decision

Persistence + the temporal model live as a **native portable store baked into `goldengraph-core`** (pyo3-free), NOT as a reuse of the Python/SQLite Identity Graph v2. Rationale:

- **Portability:** the core is pyo3-free precisely so SP5 can compile it to WASM + C. A store in the core means every surface inherits persistence + time-travel; a Python-only store would strand those two capabilities off the WASM/C surfaces and break the "every capability on every surface" commitment.
- **Suite payoff — Identity Graph v3:** once the native store + bi-temporal model exist and are proven, goldenmatch's Identity Graph (Python/SQLite today) **re-platforms onto this store as v3** — a portable identity graph across the whole suite. The store is a suite substrate, not just goldengraph's persistence.

This makes the store the foundation everything downstream rides on, so the roadmap sequences **foundation-first**: the store goes next, before the LLM pipeline.

## Sequencing principle

Build the keystone (store) first; temporal queries, persisted communities, the standalone pipeline, and Identity Graph v3 all depend on it. Everything downstream then gets durability + as-of time-travel for free. Each phase is its own spec → plan → implementation cycle.

---

## Phases

### SP2 — Native portable store + bi-temporal model (the keystone)
**Surface:** Rust core (`goldengraph-core`), pyo3-free. Build (new) — highest-leverage build in the program.

- Durable graph store baked into the core: a portable snapshot format (Arrow IPC or compact binary — settled in SP2's spec) plus an append / upsert / as-of-query API.
- **Bi-temporal edges:** `t_valid` / `t_invalid` (event time) alongside ingest time, so the graph answers "what was true as of date X" and invalidates stale facts without deleting them (the Graphiti-style capability the popular frameworks lack outside Graphiti).
- **Entity persistence with stable IDs + merge/split history**, resolution-aware: a merge rewrites history rather than destroying it (subsumes the Identity Graph's core semantics).
- **Closes:** persistence, temporal model.
- **Strategic payoff:** the substrate for **Identity Graph v3**. SP2 delivers the data-model substrate (bi-temporal edges + stable IDs + merge/split history); the actual v3 re-platform (Python/SQLite migration, API-compat, suite-wide call-site moves) is a **separate goldenmatch effort, unblocked by SP2 — not a phase in this roadmap**. Listed here only as the reason the store is built native rather than reused.
- **Depends on:** SP1.

### SP3 — Community detection + global search
**Surface:** Rust kernel (`graph-core`) + host summarization (Python). Build the community kernel (new); reuse LLM glue for summaries.

- Hierarchical community detection — a **new algorithm** (Leiden vs label-propagation, settled in SP3's spec) built on the **existing graph-traversal infra** in `graph-core` (the WCC plumbing), over the resolved graph.
- LLM-generated community summaries (host-side) enabling GraphRAG-style **global** queries (map-reduce over community reports) alongside the **local** neighborhood retrieval SP1 already provides.
- **Closes:** community detection / global search.
- **Depends on:** SP2 (communities persist and are time-aware).

### SP4 — Host LLM pipeline (Python `goldengraph`) — the standalone milestone (local mode)
**Surface:** Python `goldengraph` package. Mostly reuse + glue, not new engine work.

- **LLM extraction:** text → triples (mentions + relationships) feeding the core resolver.
- **Zero-config resolution:** reuse goldenmatch's existing **Python auto-config controller** to pick scorer/threshold automatically (this is how the standalone pipeline is zero-config without a native controller — see Config posture above).
- **Synthesis:** subgraph → answer.
- **Embedding-seeded retrieval:** reuse `goldenembed-rs`; host computes seed embeddings, core does the graph walk.
- **Query surface:** NL query over the native store **plus a text-to-Cypher export adapter** for users persisting to Neo4j (text-to-Cypher is a thin LLM adapter, not a new engine).
- Reuses goldenmatch's `BudgetTracker` + LLM clients.
- **Closes:** LLM extraction, embedding-seeded retrieval, text-to-Cypher / NL query.
- **The standalone milestone is scoped to LOCAL mode:** at SP4-complete the Python package does extraction → zero-config resolution → store → local (neighborhood) retrieval → synthesis end-to-end. **Global-mode standalone (community map-reduce) requires SP3.** SP3 and SP4 are siblings off SP2; order them either way, but "full standalone incl. global search" is the SP3+SP4 union, not SP4 alone.
- **Depends on:** SP2 (store). NOT SP3 (local-mode standalone needs no community structure); SP3 adds global mode.

### SP5 — TS/WASM + C bindings (multi-surface parity)
**Surface:** WASM + C bindings of the core; TS host pipeline.

- Compile the now-complete core (store + temporal + community kernels) to WASM + C; golden-vector parity-gated (the SP1 cross-binding contract, extended per phase).
- Honors "every capability on every surface" for all the new features.
- **Caveat:** these surfaces are **explicit-config** (no zero-config) until the native controller port (Future) lands — the Python auto-config controller SP4 reuses does not cross to WASM/C. The bound *engine* (store, temporal, community, retrieval) is at full parity; only the zero-config layer waits.
- **Depends on:** SP2 + SP3 (the **core/kernel** work — store, temporal, community). NOT SP4: SP4 is host Python and adds no new core surface for the bindings to bind.

### SP6 — The proof (eval + head-to-head)
**Surface:** benchmark harness (extends ER-KG-Bench).

- Resolution-isolated A/B (exact vs goldenmatch resolution, pipeline otherwise identical) + framework head-to-head (vanilla LlamaIndex PGI / LightRAG / Microsoft GraphRAG vs goldengraph) on a curated QA corpus.
- Turns "we beat them on ER" into citable numbers.
- **Depends on:** SP4 (needs the full pipeline).

### Future
Port the zero-config auto-config controller to native, so the native resolver is zero-config and the host-supplied-ids path becomes optional (carried over from the SP1 spec's roadmap). **This is specifically what brings zero-config to the WASM/C surfaces (SP5);** until then SP4's Python package is the only zero-config surface (via the reused Python controller).

---

## Dependency shape

```
SP1 (shipped) ──► SP2 store ──┬──► SP3 community / global search ──┬──► SP5 TS/WASM + C
                              │    (core kernel)                   │    (binds SP2+SP3 core)
                              │                                    │
                              ├──► SP4 LLM pipeline ───────────────┴──► SP6 proof
                              │    (Python host; local mode)            (needs SP4; SP3 for global)
                              │
                              └──► (Identity Graph v3 — separate suite effort, unblocked by SP2)
```

SP2 is the keystone: it unblocks temporal, persistence, persisted communities, the standalone pipeline (local mode), AND the Identity Graph v3 substrate. SP5 binds the *core* (SP2 + SP3 kernels); SP4 is host Python and is not a build-dependency of the bindings.

## Gap → phase coverage

| Gap | Phase | Build vs reuse |
|---|---|---|
| Persistence | SP2 | Build (native, portable) |
| Temporal model | SP2 | Build (bi-temporal edges) |
| Community detection / global search | SP3 | Build kernel (graph-core) + reuse LLM glue |
| LLM extraction | SP4 | Reuse (LLM clients + BudgetTracker) |
| Embedding-seeded retrieval | SP4 | Reuse (goldenembed-rs) |
| text-to-Cypher / NL query | SP4 | Build thin adapter |

## Decisions deferred to per-phase specs (not roadmap-shaping)

- **Community algorithm:** Leiden vs label-propagation (SP3).
- **Store format:** Arrow IPC vs compact binary (SP2).
- **NL query vs text-to-Cypher emphasis:** native-store query is primary; Cypher is an export adapter for Neo4j users (SP4).

## Decisions log (this roadmap)

1. Ambition: **full standalone GraphRAG, head-to-head** — all six gaps first-class, ER as the differentiator.
2. Persistence + temporal: **native portable store baked into `goldengraph-core`** (not Identity Graph v2 reuse), so WASM/C inherit it and it becomes the **Identity Graph v3** substrate.
3. Sequencing: **foundation-first** — the store (SP2) before the LLM pipeline.
4. text-to-Cypher: **in scope but as a thin export adapter** (SP4), not a query engine; the native store query is primary.
