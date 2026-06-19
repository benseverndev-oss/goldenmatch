# Six-vertical extension roadmap

**Date:** 2026-06-19
**Status:** Planning (tracked in GitHub milestones #1–#6)
**Scope:** Six adjacent "hot tech" verticals GoldenMatch can extend into beyond the existing knowledge-graph / identity work, each taken to a **production / compliance-grade** done bar.

## Why these six

GoldenMatch already ships the primitives several hot areas are starving for: probabilistic (Fellegi-Sunter) matching, blocking, embedding-aware scoring, WCC clustering, a durable identity graph, an LLM-as-adjudicator, and a two-party PPRL protocol. Each track below turns an existing primitive into a full vertical.

These are **six independent tracks** — no cross-area sequencing. Milestones can be picked up in any order based on what is hottest. Phases *within* a track are ordered.

Labels: each track has a filter label (`agent-memory`, `dedup-scale`, `rag`, `fraud-aml`, `pprl`, `cdp-mdm`); epics carry `roadmap`.

---

## M1 — Agent Memory & Identity Layer

- **Milestone:** [#1](https://github.com/benseverndev-oss/goldenmatch/milestone/1) · **Epic:** [#1073](https://github.com/benseverndev-oss/goldenmatch/issues/1073) · **Label:** `agent-memory`
- **Start state:** MCP/A2A/AgentSession, Learning Memory (corrections store + learner), and review-gating all exist. Agent state is ephemeral per request; agents can only *read* the identity graph.
- **Done bar:** persistent cross-invocation agent state, agent-*writable* identity ops with guardrails, cross-session entity accumulation, formal confidence propagation, and a tamper-evident multi-agent audit log.

| # | Phase |
|---|---|
| [#1074](https://github.com/benseverndev-oss/goldenmatch/issues/1074) | Durable agent session & task store |
| [#1075](https://github.com/benseverndev-oss/goldenmatch/issues/1075) | Agent-writable identity operations |
| [#1076](https://github.com/benseverndev-oss/goldenmatch/issues/1076) | Cross-session entity accumulation |
| [#1077](https://github.com/benseverndev-oss/goldenmatch/issues/1077) | Confidence & uncertainty propagation + escalation |
| [#1078](https://github.com/benseverndev-oss/goldenmatch/issues/1078) | Multi-agent shared memory + provenance + audit log |
| [#1079](https://github.com/benseverndev-oss/goldenmatch/issues/1079) | Agent-memory eval harness + demo |

---

## M2 — Training-Data Dedup at Scale

- **Milestone:** [#2](https://github.com/benseverndev-oss/goldenmatch/milestone/2) · **Epic:** [#1080](https://github.com/benseverndev-oss/goldenmatch/issues/1080) · **Labels:** `dedup-scale`, `performance`
- **Start state:** blocking, Fellegi-Sunter scoring, native Rust kernels, and distributed WCC exist, proven to 25M single-node. Accuracy-oriented and structured-record-shaped; no MinHash/LSH or document near-dup path.
- **Done bar:** a throughput tier — MinHash/LSH sketching, document near-dup blocking, sketch-then-verify, distributed billion-scale dedup with corpus adapters, a product surface, and a CI perf gate.

| # | Phase |
|---|---|
| [#1081](https://github.com/benseverndev-oss/goldenmatch/issues/1081) | MinHash / LSH sketch kernel |
| [#1082](https://github.com/benseverndev-oss/goldenmatch/issues/1082) | Document / text near-dup blocking path |
| [#1083](https://github.com/benseverndev-oss/goldenmatch/issues/1083) | Sketch-then-verify throughput execution plan |
| [#1084](https://github.com/benseverndev-oss/goldenmatch/issues/1084) | Distributed billion-scale dedup |
| [#1085](https://github.com/benseverndev-oss/goldenmatch/issues/1085) | Corpus-dedup product surface |
| [#1086](https://github.com/benseverndev-oss/goldenmatch/issues/1086) | Throughput benchmark + CI perf gate |

---

## M3 — RAG Entity Canonicalization

- **Milestone:** [#3](https://github.com/benseverndev-oss/goldenmatch/milestone/3) · **Epic:** [#1087](https://github.com/benseverndev-oss/goldenmatch/issues/1087) · **Labels:** `rag`, `llm`
- **Start state:** embedding providers (local/Vertex/OpenAI/Snowflake/in-house ONNX), an ANNBlocker (FAISS, manual), vector-similarity scoring, and an LLM-as-adjudicator exist. No persistent vector index, no retrieval API; semantic blocking is manual-only.
- **Done bar:** persistent vector index, a retrieval API, auto-enabled semantic blocking, LLM canonicalization with provenance, an entity-aware RAG surface, and embedding ops (drift + per-field models + eval).

| # | Phase |
|---|---|
| [#1088](https://github.com/benseverndev-oss/goldenmatch/issues/1088) | Persistent vector index |
| [#1089](https://github.com/benseverndev-oss/goldenmatch/issues/1089) | Semantic retrieval API (retrieve_similar_records) |
| [#1090](https://github.com/benseverndev-oss/goldenmatch/issues/1090) | Auto-enabled semantic blocking |
| [#1091](https://github.com/benseverndev-oss/goldenmatch/issues/1091) | LLM entity canonicalization |
| [#1092](https://github.com/benseverndev-oss/goldenmatch/issues/1092) | Entity-aware RAG surface |
| [#1093](https://github.com/benseverndev-oss/goldenmatch/issues/1093) | Embedding ops: drift, per-field models, eval |

---

## M4 — Fraud / AML / KYC / Sanctions Screening

- **Milestone:** [#4](https://github.com/benseverndev-oss/goldenmatch/milestone/4) · **Epic:** [#1094](https://github.com/benseverndev-oss/goldenmatch/issues/1094) · **Label:** `fraud-aml`
- **Start state:** connected components, multi-table graph-ER with evidence propagation, and Fellegi-Sunter matching exist. No watchlist/sanctions screening, no graph risk heuristics, no one-record-vs-list API, no m/u log-odds audit trail.
- **Done bar (compliance):** a one-to-many screening API, sanctions/PEP ingestion with refresh, graph risk analytics, FS m/u explainability, fraud-ring detection, and SAR-ready case management with a tamper-evident audit export.

| # | Phase |
|---|---|
| [#1095](https://github.com/benseverndev-oss/goldenmatch/issues/1095) | One-to-many screening API |
| [#1096](https://github.com/benseverndev-oss/goldenmatch/issues/1096) | Sanctions / PEP list ingestion + refresh |
| [#1097](https://github.com/benseverndev-oss/goldenmatch/issues/1097) | Graph risk analytics |
| [#1098](https://github.com/benseverndev-oss/goldenmatch/issues/1098) | Compliance-grade explainability + match-decision audit |
| [#1099](https://github.com/benseverndev-oss/goldenmatch/issues/1099) | Fraud-ring / shell-company detection |
| [#1100](https://github.com/benseverndev-oss/goldenmatch/issues/1100) | Case management + SAR-ready audit export |

---

## M5 — PPRL & Data Clean Rooms

- **Milestone:** [#5](https://github.com/benseverndev-oss/goldenmatch/milestone/5) · **Epic:** [#1101](https://github.com/benseverndev-oss/goldenmatch/issues/1101) · **Label:** `pprl`
- **Start state:** two-party CLK (Bloom filter) linkage is production-wired in Python and TS, with auto-config and post-linkage clustering. SMC is simulated, two-party only, no clean-room service or linkage audit log.
- **Done bar (production/compliance):** multi-party (3+) linkage, a true SMC backend, key management with rotating salts, a hosted clean-room orchestration service, a tamper-evident disclosure log, and a privacy/leakage eval.

| # | Phase |
|---|---|
| [#1102](https://github.com/benseverndev-oss/goldenmatch/issues/1102) | Multi-party (3+) linkage protocol |
| [#1103](https://github.com/benseverndev-oss/goldenmatch/issues/1103) | True SMC backend |
| [#1104](https://github.com/benseverndev-oss/goldenmatch/issues/1104) | Key management + rotating salts |
| [#1105](https://github.com/benseverndev-oss/goldenmatch/issues/1105) | Clean-room orchestration service |
| [#1106](https://github.com/benseverndev-oss/goldenmatch/issues/1106) | Linkage audit + cross-party disclosure log |
| [#1107](https://github.com/benseverndev-oss/goldenmatch/issues/1107) | Clean-room eval + leakage test suite + demo |

---

## M6 — CDP / MDM Identity Resolution

- **Milestone:** [#6](https://github.com/benseverndev-oss/goldenmatch/milestone/6) · **Epic:** [#1108](https://github.com/benseverndev-oss/goldenmatch/issues/1108) · **Label:** `cdp-mdm`
- **Start state:** Identity Graph v2 is functionally complete (durable graph, resolve/absorb/merge, 6 surfaces, conflict logging). Batch-only; no cross-device model, fixed survivorship, no per-cell provenance, no stewardship workflow.
- **Done bar (production):** streaming incremental resolution, a cross-device/channel stitching model, survivorship learning with per-cell provenance, cross-run entity stabilization (Identity v3), a conflict-mediation workflow, and distributed identity at 50M+ with stewardship ops.

| # | Phase |
|---|---|
| [#1109](https://github.com/benseverndev-oss/goldenmatch/issues/1109) | Streaming / micro-batch incremental resolution |
| [#1110](https://github.com/benseverndev-oss/goldenmatch/issues/1110) | Cross-device / channel stitching model |
| [#1111](https://github.com/benseverndev-oss/goldenmatch/issues/1111) | Survivorship learning + per-cell golden-record provenance |
| [#1112](https://github.com/benseverndev-oss/goldenmatch/issues/1112) | Cross-run entity stabilization (Identity v3) |
| [#1113](https://github.com/benseverndev-oss/goldenmatch/issues/1113) | Conflict mediation workflow |
| [#1114](https://github.com/benseverndev-oss/goldenmatch/issues/1114) | Distributed identity at 50M+ + MDM ops |

---

## Cross-cutting capability

The thread tying most tracks together is **LLM-as-adjudicator for hard pairs** (the Ditto / LM-based entity-matching line). It already exists for borderline scoring (`core/llm_scorer.py`) and is the lever that makes M1, M3, M4, and M6 stronger. Treat it as a shared dependency rather than a seventh track.
