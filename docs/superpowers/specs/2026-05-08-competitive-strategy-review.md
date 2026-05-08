# Golden Suite — Competitive Strategy Review

**Date:** 2026-05-08
**Author:** review pass requested on `claude/review-competitive-strategy-zy5de`
**Scope:** `D:\show_case\goldenmatch` post-fold monorepo (Python + TS + Rust + dbt + Actions). Headline package goldenmatch v1.9.0; suite-wide.
**Audience:** decision-maker. Pick what to fund next.

This is a strategy doc, not a plan. Each direction below has a one-paragraph thesis, the evidence behind it, what shipping it actually means, and a frank effort estimate. Pick 3–5 to sequence; many are mutually compatible.

---

## TL;DR

GoldenMatch is already top-2 on F1 against the OSS field (Splink / Dedupe / RecordLinkage / Zingg) on bibliographic and personal-record benchmarks, and it is the only OSS ER tool I am aware of that ships a self-verifying introspective auto-config controller, a cross-language correctness parity harness, and an MCP/A2A surface in v1. The gap to private vendors (Tamr, Reltio, Senzing, Quantexa, Tilores, AWS Entity Resolution) is **not accuracy** — it is **scale, governance, identity-graph reasoning, reference data, and a managed-service GTM**.

The five highest-ROI next directions, in priority order:

1. **Lift the in-memory ceiling** so a single 64GB box handles 10M rows by default (not as an opt-in). Today's 500K OOM cliff costs us against any private vendor whose first slide is "we did 100M".
2. **Identity graph as a first-class output**, not a clustering byproduct. Cross-time, cross-source entity edges with provenance — directly contests Senzing / Tilores / Quantexa, who own this category.
3. **Postgres extension to parity** — promote `goldenmatch-extensions` from "Postgres+DuckDB UDFs" to "Postgres-native entity resolution" with incremental matching, stored configs, and ANN index living in the database. SQL-only buyers will install a Postgres extension before they install a Python library.
4. **Public benchmark leaderboard (DQBench public + hosted)**. We already cite scores; making it the public reference (Hugging-Face-for-ER) anchors all future comparisons on our turf.
5. **Managed cloud SaaS (`goldenmatch.cloud`)** with row-based pricing and BYO LLM. A single price umbrella against $250K Tamr / $400K Reltio is the only durable revenue path; the OSS-as-funnel motion needs the funnel to lead somewhere.

Lower-priority but cumulative: stewardship UX (multi-user web + RBAC), reference-data plug-ins (USPS/NAICS/OpenCorporates), streaming-first parity, vertical accuracy packs, dbt-native ER, security/compliance posture (SOC 2 readiness statement). Detailed below.

---

## Where we stand (honest assessment)

### What we already win on
- **Zero-config that beats hand-tuned.** v1.8.0 controller drove DBLP-ACM 0.51→0.964 zero-config (hand-tuned ceiling 0.918). Febrl3 0.944. NCVR 0.972. No competitor — OSS or paid — claims this. ([packages/python/goldenmatch/CHANGELOG.md], README v1.8.0 callout.)
- **Polyglot parity at the byte level.** The 4-decimal scorer parity harness in `packages/typescript/goldenmatch/tests/parity/` and the byte-identical SHA-256 Learning Memory format are unique. Splink is Python-only; Dedupe is Python-only; Senzing is C/Java with thin language bindings; Tamr is server-side.
- **AI-native by default.** 35+ MCP tools, A2A skills, REST API, and the MCP server is hosted on Smithery. No incumbent OSS or vendor ships an MCP-driveable ER engine; this is a 2026-shaped wedge.
- **Privacy-preserving record linkage shipped, not promised.** PPRL auto-config 92.4% F1 on FEBRL4 with per-field HMAC. Tamr/Reltio talk about it; we benchmark it.
- **Composable suite.** Five packages (Check / Flow / Match / Pipe / InferMap) with a dbt package and a GitHub Action — most ER vendors have one of these and call it a "platform".

### What we credibly don't win on
- **Throughput at scale.** README says "OOM in-memory >500K rows; use DuckDB or Ray for 1M+". Splink scales to ~1B via Spark/DuckDB out of the box. Senzing handles 10M+ on a single node by design. AWS Entity Resolution is "scale is not your problem". Our 7,823 rec/s at 100K is on a laptop and falls off a cliff afterward.
- **Identity graph + temporal.** `core/graph_er.py` and `core/graph.py` exist, but pair-clustering is not a graph product. Quantexa, Senzing, Tilores own this segment. We have the bones; we have not built the building.
- **Stewardship at >1 user.** Web workbench is explicit "single-process, no auth — for the dev-on-a-laptop case". Reltio/Tamr's deployment moat is multi-user RBAC + audit + workflow, not their matcher.
- **Reference data.** Senzing ships `libpostal`-equivalent normalizers, business-name dictionaries, watchlist matching. We have 7 domain packs, none with real reference data behind them.
- **Distribution.** No Snowflake Native App, no Databricks Partner Connect, no AWS Marketplace, no Fivetran connector, no listing on dbt Hub for goldenmatch (we have it for goldencheck). We are a `pip install` business.

### What we're roughly even on
- **Probabilistic matching (Fellegi-Sunter).** Splink is the SOTA for this single pattern; we ship it as one strategy among many. Tied for capability on PII; we lose a few F1 points but win on portability.
- **Active learning / labeling.** Dedupe and Zingg make this central; we have boost tab + Learning Memory. Functionality parity, UX gap.
- **Cost on LLM-augmented ER.** Our $0.04 LLM budget on Abt-Buy is competitive. We don't have a reason customers should pay for our LLM orchestration vs roll-their-own; the moat is the budget cap + cached votes + Learning Memory replay, which is real but undermarketed.

---

## The competitive landscape, segment by segment

### 1. OSS libraries (the floor)
| Tool | Pattern | Where they win | Where we win |
|---|---|---|---|
| **Splink** | Probabilistic + Spark/DuckDB | PII, scale, EM training | Non-PII data, zero-config, polyglot |
| **dedupe** (Python) | ML + active learning | Active learning UX | Out-of-the-box accuracy, no labels needed |
| **RecordLinkage Toolkit** | Classical | DBLP-ACM (single benchmark) | Everything else |
| **Zingg** | Spark + ML | Spark shops | Single-machine, polyglot, LLM, MCP |
| **JedAI** (Java) | Toolkit | Java shops | Modern stack, zero-config |
| **fuzzymatcher / py_stringmatching** | Fuzzy primitives | — | Whole-pipeline, golden records |

**Posture:** maintain the lead. Don't chase Splink's PII numbers (Splink-style EM is already a strategy in `core/probabilistic.py`); chase coverage breadth and zero-config primacy.

### 2. Cloud-managed ER (rising)
- **AWS Entity Resolution** — managed service, integrated with Glue/SageMaker, machine-learning + rule-based, opaque pricing.
- **Google Cloud Entity Reconciliation** — Knowledge Graph-anchored.
- **Snowflake / Databricks native** — both shipped first-party ER UDFs in 2025–2026.

**Posture:** these win on integration and lose on portability + accuracy + price. Our wedge is "the same engine in your laptop, your edge worker, your Postgres, and your Spark — your data never leaves your tenant unless you tell it to". Snowflake Native App listing is the highest-leverage single GTM move (see direction #6 below).

### 3. MDM enterprise (the money)
- **Tamr** — ML + human-in-the-loop, $250K–$2M deals, 6-month deployments.
- **Reltio** — cloud MDM, hub-and-spoke, $400K+ ARR typical, Salesforce-native.
- **Informatica MDM / IDQ** — incumbent, slow but trusted.
- **Stibo, Ataccama, SAP MDG, Profisee** — long tail; each owns a vertical or geography.

**Posture:** do **not** build a competing MDM hub. We lose. Position GoldenMatch as the *engine* customers use to escape these vendors, or as the *engine* a new MDM startup would embed (we already license MIT). The path to revenue here is being acquired or being the matching layer inside a faster-moving MDM.

### 4. Identity graph / KYC / fraud (the moat)
- **Senzing G2** — semi-OSS, identity graph, deterministic + probabilistic, 30+ years of reference data.
- **Quantexa** — graph-based contextual decisioning, fraud/AML/KYC.
- **Tilores** — entity-API-as-a-service, GraphQL identity graph.
- **TigerGraph + Neo4j ER recipes** — DIY graph ER on a graph DB.

**Posture:** the most defensible mid-term direction (see direction #2 below). Today our `graph_er` module is rendered after clustering; an identity graph is the *primary product*, with edges, time, source-attribution, and re-resolution on new evidence. Senzing has spent 25 years on this; we can't win the dictionary game, but we *can* win the AI-native + composable + open-source game.

### 5. Adjacent / DQ tooling
- **Monte Carlo, Atlan, Alation, dbt Cloud, Soda** — observe data, don't fix it.
- **Talend, Trifacta** — wrangling, not ER.

**Posture:** these are partners, not competitors. dbt-goldencheck is already a partnership-shaped surface; extend with `dbt-goldenmatch` (DuckDB-based ER in dbt — referenced in `goldenmatch/CLAUDE.md` but not on PyPI yet).

---

## Strategic directions, ordered by ROI

Each item: **thesis · evidence · ship · effort · risk**.

### Direction 1 — Lift the single-node throughput ceiling

**Thesis.** The 500K-row in-memory cliff is the single biggest credibility tax we pay in pilots. Lifting the default ceiling to ~10M on a 64GB box (no `--backend duckdb` flag, no Ray, no chunking) would close half the gap to AWS/Splink in one release.

**Evidence.**
- `goldenmatch/CLAUDE.md` — "1M records: OOM in-memory — use DuckDB backend or chunked processing for >500K records".
- The 2026-05-04 audit (`docs/superpowers/specs/2026-05-02-performance-audit-checklist.md`) found that the matchkey-transform hoist gave 1.22× wall, well below what static counts implied. **Actual wall-clock measurement on the 100K → 1M scale curve has not been done end-to-end since the audit's lessons landed.** Do that first.
- Existing infrastructure: DuckDB backend (`backends/duckdb_backend.py`), chunked processing (`core/chunked.py`), Polars throughout.

**Ship.**
1. Re-do the bench at 100K, 500K, 1M, 5M with profiling + RSS tracking. Median 5 runs (audit lesson). Identify the actual bottleneck — likely the pair-list growth in the scorer, not Polars.
2. Make DuckDB backend the default above a measured row threshold (e.g., 250K), transparently. The opt-in flag becomes opt-out.
3. Add streaming pair-emission so we never hold N² pairs in memory.
4. Acceptance: 10M rows on a 64GB box without OOM, end-to-end, no flags.

**Effort.** 2–4 weeks engineering, 1 week benchmarking. Mostly reuse + threshold tuning; the worst case is a bigger refactor of `core/pipeline.py` to honor a `pair_emit` interface.

**Risk.** Medium. Hidden allocations in transform paths (we just hoisted matchkey transforms; profile for the next set). DuckDB-as-default could regress small-data wall time — gate on row count.

---

### Direction 2 — Identity graph as a primary product

**Thesis.** A clustered output (the `cluster_id → members` dict we ship today) is a snapshot. An *identity graph* is a queryable, append-only entity store with edges, time, source attribution, and re-resolvability when new evidence arrives. Senzing/Tilores/Quantexa have built whole companies around this single insight. We have ~30% of the bones (`core/graph_er.py`, `core/lineage.py`, Learning Memory) and we should finish the building.

**Evidence.**
- `core/graph_er.py` exists. `core/lineage.py` records per-field provenance. `core/memory/store.py` already has the SQLite schema for persistent decisions. We have all the pieces; they don't compose into a graph product.
- Buyer demand signal: every enterprise pilot mentions "person → account → device → transaction" linkage. This is graph-shaped, not pair-shaped.
- Cross-time is unsolved in the OSS world. Splink doesn't do it. Dedupe doesn't do it. AWS Entity Resolution doesn't do it well.

**Ship.**
1. **`goldenmatch graph` subcommand + Python API.** `gm.graph.upsert(records, source=...)` returns a `GoldenGraph` whose nodes are entities, edges are evidence (each scored pair), and time is first-class.
2. **Re-resolution on append.** When new records arrive, only re-score against the affected entity neighborhood. The streaming module + Postgres ANN index get us close; wire them through.
3. **Edge provenance, not just pair scores.** Each edge knows source, score, scorer used, transform chain, timestamp. This is what makes a graph defensible against Senzing.
4. **Query surface.** `graph.find(name="...", at="2024-01")`, `graph.timeline(entity_id)`, `graph.merge_history(entity_id)`. GraphQL is *not* required v1; a Python + REST surface beats Tilores' GraphQL on ergonomics.
5. **Storage backends.** Start with SQLite + DuckDB. Postgres-native via the Rust extension. Neo4j adapter as a v2 wedge.

**Effort.** 8–12 weeks for v1 (graph store + re-resolution + REST). The hard part is the data model — get it right once, evolve under it.

**Risk.** High-reward, high-effort. The data model decision is permanent; spend a week on the design (a real spec, not a sketch). Don't try to be a graph database; be an identity-graph application that uses one.

---

### Direction 3 — Postgres extension to parity (then promote to headline)

**Thesis.** SQL-first buyers (the long tail of analytics teams) install a Postgres extension before they install a Python library. The Rust extension is currently a satellite; promote it to a coequal headline product with stored configs, incremental matching, and an ANN index that lives in the database.

**Evidence.**
- `packages/rust/extensions/` exists; pgrx + DuckDB UDFs shipped.
- README shows seven SQL functions; Postgres parity to the Python API is roughly 30% (no incremental, no Learning Memory replay, no PPRL, no review queue).
- "DB-native ER" is a category Senzing and Splink-on-DuckDB are pushing into; we have the codebase advantage but not the polish.

**Ship.**
1. **`goldenmatch_run(config jsonb, source regclass)` returns a result set.** Today we have helper UDFs; we need an end-to-end orchestrator UDF that mirrors `gm.dedupe()`.
2. **Incremental matching as a Postgres trigger.** Insert a row into a watched table → ANN-blocking → score-against-cluster → write back. The Python `core/streaming.py` is the model.
3. **ANN index as a Postgres index type** (long-term, pgvector-adjacent). v1: ANN index in a sidecar table with maintenance triggers.
4. **Stored configs as `goldenmatch_configs` table** with versioning. Lets a DBA review "the live ER config" in SQL.
5. **DuckDB MotherDuck publishing.** A DuckDB extension on the MotherDuck registry with the same UDFs is a two-week shippable.

**Effort.** 6–10 weeks (parity work), then ongoing. Distinct from direction 2 — they compose well: identity graph nodes/edges are Postgres tables, the extension queries them.

**Risk.** Medium. pgrx local-build issues are documented in `goldenmatch/CLAUDE.md` (`pgrx cannot build locally — needs libclang/LLVM. Use CI`). The dev loop is slow; budget for it.

---

### Direction 4 — Public benchmark leaderboard

**Thesis.** Whoever owns the reference benchmarks owns the conversation. Hugging Face owns model leaderboards. Nobody owns the ER leaderboard yet. DQBench (referenced in our README, score 95.30) should be the public artifact, hosted, with a submission flow.

**Evidence.**
- DQBench is already in our README with a real number, but the linked repo (`benzsevern/dqbench`) is referenced not promoted.
- Existing benchmarks site templates (HF leaderboards, Papers With Code) are well-trodden.
- Nobody in the ER OSS world has done this. RecordLinkage Toolkit's docs reference benchmarks; nobody hosts a leaderboard.

**Ship.**
1. **Public DQBench site.** Static site, GitHub Pages, leaderboard JSON committed in-repo. Submissions are PRs.
2. **Reproducible run scripts** for every entry. Splink/Dedupe/Zingg already have public configs for DBLP-ACM/Febrl; we run them on the same hardware and publish.
3. **Per-segment scoring**: PII, bibliographic, product, business records, healthcare, PPRL. Each segment has its own podium.
4. **"Try It" button on every result** — Colab notebook with a tagged run that reproduces.

**Effort.** 2–4 weeks for v1. Largely a docs+CI project, not engineering.

**Risk.** Low. Worst case it doesn't catch on; we still benefit from the discipline of running competitors regularly. Best case it becomes the citation other vendors have to reference.

---

### Direction 5 — Managed cloud SaaS (`goldenmatch.cloud`)

**Thesis.** OSS-as-funnel only works if the funnel leads somewhere. A multi-tenant managed service with row-based pricing — say, $0.10 per 1K rows matched, free under 100K/mo — is the only durable revenue motion that doesn't require building an enterprise sales team. The price umbrella vs Tamr ($250K) and Reltio ($400K) is not 10×, it's 100×.

**Evidence.**
- We already host an MCP server on Railway (`goldenmatch-mcp-production.up.railway.app`). The infra story is partly written.
- AWS Entity Resolution charges per matching record; their pricing is a ceiling we sit comfortably under.
- A Smithery-hosted MCP is a great free trial; converting "MCP user → cloud account" is a single-click flow we don't currently have.

**Ship.**
1. **A real authenticated tenant.** Today the Railway MCP is anonymous; add API keys.
2. **Row-billed dedup endpoint.** `POST /v1/dedupe` with usage metering.
3. **Bring your own LLM.** Keys never leave the tenant; we provide budget caps and caching.
4. **Stripe integration.** Free tier, paid tier, enterprise tier (with PPRL + SOC 2 statement).
5. **A landing page that says "Splink, Tamr, and AWS Entity Resolution priced under one roof" with the actual price comparison.**

**Effort.** 6–10 weeks for a v1 cloud + 4 weeks of GTM (landing page, pricing page, signup flow). 1 FTE for ops thereafter.

**Risk.** High in the sense that running a cloud is a real commitment (uptime, on-call, billing disputes). Manageable if scoped to "we host the matcher; you keep your data and your LLM keys".

---

### Direction 6 — Marketplace listings (Snowflake / Databricks / Fivetran / dbt Hub)

**Thesis.** Distribution beats accuracy. The Snowflake Native App + Databricks Partner Connect listings put us in front of every buyer who has a budget and a data warehouse. Today our distribution is `pip install`.

**Evidence.**
- Snowflake Native Apps launched 2024; ER apps are conspicuously absent.
- Databricks Partner Connect: similar gap.
- We already have Snowflake/Databricks/BigQuery connectors in `connectors/`.
- dbt-goldencheck is on dbt Hub; goldenmatch is not.

**Ship.**
1. **Snowflake Native App** — wraps `dedupe_df` over Snowpark; bills via Snowflake's marketplace. ~4 weeks.
2. **Databricks Partner Connect listing** — DBR notebook + cluster init. ~3 weeks.
3. **`dbt-goldenmatch` package** — DuckDB-based; the DB extension story (direction 3) feeds this. ~3 weeks.
4. **Fivetran custom connector** — synced reverse-flow ER on the way in. ~2 weeks.
5. **AWS Marketplace listing** — Lambda layer + container. ~3 weeks.

**Effort.** ~15 weeks total but parallelizable across listings. Each is its own paperwork mountain (Snowflake takes 6+ weeks of partner review).

**Risk.** Low engineering risk, high partnership-bureaucracy risk. Start the Snowflake review process now even if the app isn't ready — the queue is the gating factor.

---

### Direction 7 — Active learning UX, made central not optional

**Thesis.** Dedupe and Zingg both make active learning the headline. We have it (`core/active_sampling.py`, boost tab in TUI), but it's discovery-gated. A web UI flow that surfaces the 20 most informative borderline pairs and writes labels into Learning Memory in one session is a credibility move at every demo.

**Evidence.**
- `core/active_sampling.py` exists. Learning Memory writes are already wired through the web inspector ("Label pairs (mirrors to Learning Memory)" — README).
- The transformation from "label 10 pairs in TUI" to "label 20 pairs in web" is mostly UX surfacing.
- Customer pattern: the first thing every evaluator does is feed in their own corrections. We should make it 90 seconds.

**Ship.**
1. **`/label` web page** with confusion-matrix-prioritized pairs (most-uncertain first, balanced by cluster).
2. **Live F1 update** as labels are added — re-evaluate against the held-out labeled set.
3. **Threshold-learner trigger at 10 corrections** (already implemented; surface the prompt).
4. **Export the labeled set as a portable `labels.jsonl`** that any GoldenMatch project can replay.

**Effort.** 2–3 weeks. Mostly frontend; engine is in place.

**Risk.** Low. Worst case it doesn't change conversion; cost is small.

---

### Direction 8 — Reference data plug-ins

**Thesis.** Senzing's moat is 25 years of name dictionaries, address normalizers, and watchlists. We can never out-curate them, but we can match them on the 80% case using bundled OSS reference data + clean APIs. A "people pack" with US Census surnames + libpostal + USPS CASS-equivalent + SSA name frequencies, benchmarked, would let us claim parity for the 90% of cases that don't need defense-grade reference.

**Evidence.**
- `goldenmatch/domains/` has 7 packs but no reference data behind them — they're rule sets, not lookups.
- libpostal (MIT) — address parsing.
- US Census Bureau — surname/given-name frequency tables.
- OpenCorporates — business-name normalization (CC-BY).
- SEC EDGAR — public company tickers/CIKs.
- NAICS / SIC — industry codes.
- All free, all incorporable, none currently bundled.

**Ship.**
1. **`goldenmatch[reference-people]`** extra: bundled name-frequency lookups, given-name aliases (William↔Bill), nickname tables.
2. **`goldenmatch[reference-business]`** extra: legal-form normalization (Inc/LLC/GmbH), industry code lookups.
3. **`goldenmatch[reference-address]`** extra: libpostal binding (we already have it as an opt-in for `pyap`/`usaddress`).
4. **Benchmarks**: NCVR with reference-people pack, OpenCorporates merge with reference-business pack. Publish on the leaderboard (direction 4).

**Effort.** 4–6 weeks. Half is data-licensing diligence; half is integration.

**Risk.** Medium. Each reference dataset has its own license terms; some won't permit redistribution. Start with US-Census + libpostal.

---

### Direction 9 — Stewardship governance (multi-user web + RBAC + audit)

**Thesis.** Today's web workbench is "single-process, no auth — for the dev-on-a-laptop case". Every enterprise pilot fails on this. A multi-user web with at-minimum email-based auth, role separation (admin / steward / reviewer), and an immutable audit log is a hard gate for any deal over $50K.

**Evidence.**
- `web/` README explicitly says single-user, dev-only.
- Reltio's product moat is "the steward queue"; ours is functionally equivalent in `core/review_queue.py` but unhardened.
- This direction is a deal-unblocker, not a moat. Don't over-invest, but ship it.

**Ship.**
1. **Auth layer** — start with magic-link email + GitHub SSO. Don't build SAML v1.
2. **Role model** — admin / steward / reviewer / read-only.
3. **Audit log** — append-only table of every label, merge, unmerge, config save. Already half-built in `core/lineage.py`.
4. **Review queue UI** — the existing `core/review_queue.py` API → web pages with assignment + SLA timer.
5. **Multi-project tenancy** — projects already exist as a directory layout; promote to first-class.

**Effort.** 4–6 weeks. The `[web]` extra picks up dependencies but the architecture decision is single-process today; lifting to multi-process is the real work.

**Risk.** Medium. Authentication done badly is worse than none. Use a well-known library (Authlib + FastAPI-Users) and resist the urge to build.

---

### Direction 10 — Streaming/CDC parity with offline

**Thesis.** "Same config that scored your batch overnight scores the next inserted row in <100ms" is a positioning we can almost claim. `core/streaming.py` exists. `core/match_one.py` exists. The Postgres extension (direction 3) makes this trigger-driven. Position GoldenMatch as the only ER tool where batch and streaming use the *same scorer code*.

**Evidence.**
- `core/streaming.py` and `core/match_one.py` are both shipped.
- `goldenmatch/CLAUDE.md` notes `match_one()` returns empty list for exact matchkeys — fixable.
- Reltio and Tilores both market real-time; both are slow relative to a co-located Postgres trigger.

**Ship.**
1. **`gm.match_one_async()`** with sub-100ms p95 on a Postgres-co-located deployment.
2. **CDC integration** — Debezium → Kafka → `match_one_async`. Reference deploy.
3. **Streaming Learning Memory replay** — corrections apply to streaming events identically to batch.
4. **Latency benchmark** alongside the accuracy leaderboard.

**Effort.** 3–5 weeks v1.

**Risk.** Medium. Latency claims are easy to make and hard to defend; benchmark them publicly.

---

### Direction 11 — Vertical accuracy packs with measurable wins

**Thesis.** "7 domain packs" is currently a footnote. Pick three (people, business, healthcare) and turn each into a measurable benchmark win — a published F1 number against the segment's reference dataset, with a "vs Senzing/Tilores/Tamr" cell where we have third-party numbers.

**Evidence.**
- Domain packs in `goldenmatch/domains/` are configs, not benchmarks.
- NCVR (people) → we already have 0.972; cite it as "vs Senzing G2 published [X]".
- OpenCorporates (business) → no published GoldenMatch number.
- MIMIC-III patient deduplication (healthcare) → academic benchmark, no GoldenMatch entry.

**Ship.**
1. Run benchmarks for the three packs.
2. Publish to the leaderboard (direction 4).
3. Each pack ships with a "what this pack does" doc and a "against [vendor], on [dataset]" table.

**Effort.** 3 weeks. Mostly running benchmarks and writing the page.

**Risk.** Low.

---

### Direction 12 — Compliance posture (SOC 2 readiness statement, HIPAA mapping, GDPR)

**Thesis.** A two-page "security & compliance" doc is enough to unblock 60% of mid-market pilots. Going for actual SOC 2 Type II costs $25–50K and 4–6 months; the *readiness statement* costs a week.

**Ship.**
1. **`/security` page** — encryption (TLS in MCP, at-rest in Postgres if user encrypts), data flow (everything is on-prem unless you opt into cloud), no-PII guarantee for telemetry (telemetry is opt-in if at all).
2. **HIPAA appendix** — PPRL is the actual differentiator; lean on it. Document the BAA-shaped path.
3. **GDPR appendix** — right to erasure means rolling back Learning Memory + lineage; document.
4. **`docs/threat-model.md`** — explicit list of what we protect against and what we don't.

**Effort.** 1–2 weeks.

**Risk.** Low engineering. Wording must be true; involve legal once cloud SaaS launches (direction 5).

---

## What NOT to do

These are the strategic traps:

1. **Don't build a competing MDM hub.** Tamr and Reltio have a 10-year head start on the workflow primitives (data steward queues, golden-record approval, source-priority rules) and the enterprise sales motion. Position GoldenMatch as the engine. If a buyer wants a hub, they should buy Reltio and put GoldenMatch underneath, or pick a smaller MDM vendor that embeds us.
2. **Don't try to out-curate Senzing's reference data.** They've spent 25 years on it. Ship the bundled-OSS reference packs (direction 8), be honest about the gap, beat them on price + composability + LLM-native.
3. **Don't ship more "domain packs" without benchmarks.** Three benchmarked packs beat seven unbenchmarked.
4. **Don't add new data-quality features without re-running the perf audit's measurement protocol** (`docs/superpowers/specs/2026-05-02-performance-audit-checklist.md`). The lesson — *measure wall-clock with the real workload before designing* — was paid for in shipped optimizations that turned out small. Don't pay it again.
5. **Don't fragment the polyglot story.** Every new feature must answer "what's the parity story for TS?" and "does the SQL extension expose this?". Letting Python drift ahead of TS once is a churn that costs more than skipping the feature.
6. **Don't promise scale we haven't measured.** Today's claim "10M+ records with Ray backend" is technically true and operationally weak. Either invest (direction 1) or stop saying it.

---

## Suggested 90-day cuts

If you can fund only one direction in Q3, fund **direction 1 (throughput ceiling)**. It's the single biggest credibility tax we pay, and it makes everything else more honest.

If you can fund three, sequence:

1. **Weeks 1–4: direction 1** (throughput) + start direction 4 (leaderboard) in the background. Both are mostly engineering, parallelizable across people.
2. **Weeks 5–8: direction 7** (active learning UX) + direction 11 (vertical benchmarks → leaderboard). UX and accuracy stories ship together.
3. **Weeks 9–12: direction 6** (Snowflake Native App submission — start the partner review queue now even if v1 isn't ready) + direction 9 (stewardship governance v1).

If you can fund five, add:

- **Direction 2** (identity graph) — start a real spec in week 1, shape the data model in weeks 2–4, ship v1 in weeks 5–12. This is the long-cycle bet; the others compound around it.
- **Direction 5** (cloud SaaS) — start the auth + metering work in week 5, public beta in week 12. Don't launch with strong SLAs; price to early-adopter risk.

Defer: direction 3 (Postgres extension parity) until direction 2 lands the data model, since the extension is the SQL-native shape of the same graph. Direction 8 (reference data) and 10 (streaming) are leverage multipliers for direction 2 — schedule them after.

---

## Open questions for the next strategy iteration

These are the ones I couldn't answer from the repo alone:

1. **Who is the buyer today?** OSS users are not customers. Map the last 20 inbound conversations: are they "data engineer at a startup", "data steward at a mid-market", or "ML lead at a Fortune 500"? Each implies a very different next direction.
2. **What's our existing GitHub stars curve telling us?** Stars by week + referrer would tell us which content (MCP? PPRL? Web UI?) actually drives the funnel.
3. **What's the Smithery MCP usage pattern?** If the hosted MCP is getting real traffic, that's the cloud-SaaS GTM (direction 5) signal. If it's getting laptop-developer traffic, the headline stays OSS.
4. **What benchmarks do enterprise prospects ask about by name?** The ones we measure (DBLP-ACM, Febrl, NCVR, Abt-Buy) are academic. If buyers ask "did you run on the [vendor's reference] dataset?", we should publish that.
5. **Pricing intuition for `goldenmatch.cloud`.** $0.10/1K is a guess. Survey 5 OSS-paying-for-cloud users; the right number is probably 3–10× lower than we think.

---

## Closing note

The shortest version of this doc: **GoldenMatch is technically ahead in zero-config and AI-native; commercially it's a `pip install` business. The next year's bets are about turning technical lead into distribution and trust.** None of the directions above require us to invent new science. They require us to ship things that match the ambition the README already claims.

Pick three. Sequence them. Measure them.
