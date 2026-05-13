# ER vendor comparison — GoldenMatch vs the field

**Last updated:** 2026-05-13
**Scope:** entity-resolution **engines**, both OSS and commercial. This is a reference doc, not marketing. Every claim about GoldenMatch is reproducible from this repo; claims about other vendors are sourced from their public docs as of mid-2026 and may lag behind their current release.
**Sister doc:** `docs/superpowers/specs/2026-05-08-competitive-strategy-review.md` — what we *do* with these comparisons.

If you find a factual error about any vendor below, open a PR. We'd rather be corrected than wrong.

---

## How to read this doc

- **Scorecard table** at the top — every vendor × every axis. Use to scan.
- **Per-vendor entries** below, grouped by tier (OSS engines · identity-graph · cloud-managed · enterprise MDM · research SOTA · adjacent).
- Every entry follows the same shape: snapshot · technical model · scale · privacy · reference data · AI/ML · runtime · where they beat us · where we beat them · composability.
- **Cross-cutting observations** at the end pull patterns out of the matrix.
- **Caveats** at the very end. Pricing is indicative; vendors change quickly; the matrix is a snapshot.

A "win" in the matrix below means *materially ahead with evidence*. Ties stay blank.

---

## GoldenMatch — the baseline of this doc

Before comparing, the column we're comparing *against*. Numbers are as of `goldenmatch` v1.9.0, post PR #189 / Round 5 scale audit (2026-05-12).

| Axis | GoldenMatch |
|---|---|
| **License** | MIT (every package in the suite) |
| **Languages** | Python (headline) + TypeScript (parity) + Rust (Postgres/DuckDB extension) |
| **Runtimes** | Polars (≤500K), DuckDB (500K–50M), Ray (≥50M); Postgres via pgrx; DuckDB UDFs; edge JS (Vercel Edge / Cloudflare Workers / Deno) |
| **Throughput** | 1M dedupe in 12.3 min on 4-core / 16 GB Linux (Round 5, 2026-05-12); 100K fuzzy ~39 s; 7,823 rec/s pipeline at 100K |
| **Accuracy, PII (Febrl)** | F1 0.971 (zero-config 0.944 on Febrl3) [^bench] |
| **Accuracy, bibliographic (DBLP-ACM)** | F1 0.964 zero-config (hand-tuned ceiling 0.918) [^bench] |
| **Accuracy, product (Abt-Buy)** | F1 0.722 +$0.04 LLM; 0.817 with Vertex AI + GPT-4o-mini [^bench] |
| **Accuracy, voter records (NCVR)** | F1 0.972 zero-config [^bench] |
| **Zero-config** | Introspective auto-config controller (v1.8+) with cross-run memory and LLM fallback. **Only OSS engine with a published zero-config benchmark suite.** |
| **PPRL** | Bloom-filter PPRL with auto-configuration; F1 0.924 on FEBRL4 |
| **Active learning** | `core/active_sampling.py` + boost tab; Learning Memory persistence; threshold learner triggers at 10+ corrections |
| **LLM scoring** | Budget-capped LLM scorer; cached votes via Learning Memory; opt-in |
| **Identity graph** | Cluster-level (post-hoc). No first-class graph output yet. |
| **Reference data** | None bundled. 7 domain packs (electronics, software, healthcare, financial, real-estate, people, retail) ship rules, not lookups. |
| **Explainability** | Per-pair NL prose explanations; per-field score breakdown; lineage tracking |
| **AI-native surface** | 35+ MCP tools, A2A agent skills, REST API, Smithery-hosted MCP. **Only OSS ER engine with native MCP/A2A surface.** |
| **Active dev** | Tracks daily; v1.9.0 shipped May 2026 |

The rest of this doc is "but what about X?".

---

## Scorecard at a glance

Legend: **GM** = GoldenMatch is materially ahead. **V** = vendor is materially ahead. blank = tied or non-applicable.

|  | Splink | dedupe | RecLink Toolkit | Zingg | JedAI | Magellan | fuzzymatcher | Senzing CE | Senzing Ent | Quantexa | Tilores | LexisNexis | AWS ER | GCP Ent. Recon. | Snowflake | Databricks ARC | Tamr | Reltio | Informatica | Ataccama | Stibo | Profisee | Ditto |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **OSS license** | GM | GM | GM | GM* | GM | GM | GM | GM† | V | V | V | V | V | V | V | V | V | V | V | V | V | V | GM |
| **Zero-config** | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM |
| **Polyglot (≥2 lang)** | V | GM | GM | GM | V | GM | GM | V | V | V | V | V | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM |
| **PII F1 (Febrl)** | V | | | | | | | | | | | | | | | | | | | | | | V |
| **Bib F1 (DBLP-ACM)** | GM | GM | | GM | | GM | GM | | | | | | | | | | | | | | | | V |
| **Product F1 (Abt-Buy)** | GM | GM | GM | GM | GM | GM | GM | GM | | | | | | | | | | | | | | | V |
| **Throughput ≥10M single node** | V | GM | GM | V | GM | GM | GM | V | V | V | V | V | V | V | V | V | V | V | V | V | V | V | GM |
| **PPRL** | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM |
| **Identity graph** | GM | GM | GM | GM | GM | GM | GM | V | V | V | V | V | GM | V | GM | GM | GM | V | GM | GM | GM | GM | GM |
| **Reference data** | GM | GM | GM | GM | GM | GM | GM | V | V | V | V | V | V | V | GM | GM | V | V | V | V | V | V | GM |
| **AI-native (MCP / A2A)** | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM | GM |
| **LLM-augmented scoring** | GM | GM | GM | V | GM | GM | GM | GM | | | | | V | V | V | V | V | V | | | | | V |
| **Active learning** | GM | V | GM | V | GM | V | GM | GM | GM | | GM | | | | | | V | | | | | | V |
| **Multi-runtime (DB / edge / batch)** | V | GM | GM | V | GM | GM | GM | V | V | V | V | V | V | V | V | V | V | V | V | V | V | V | GM |
| **Active dev (2025–2026)** | V | | | V | | | | V | V | V | V | V | V | V | V | V | V | V | V | V | V | V | |
| **Free for production use** | GM | GM | GM | GM* | GM | GM | GM | GM | V | V | V | V | V | V | V | V | V | V | V | V | V | V | GM |

*\* Zingg core is AGPL — viral copyleft, restrictive for commercial embedding. ZinggAI is closed.*
*† Senzing CE is Apache 2.0 wrappers around a closed core engine. OSS surface, closed brain — treat the GM tag as "OSS-license surface" not "fully OSS engine".*

Three observations from the matrix:

1. **Nobody else publishes zero-config benchmark numbers.** Every "GM" in that row reflects that this is currently uncontested ground.
2. **Throughput at ≥10M on a single node is the universal "V" against us.** Almost every paid vendor and several OSS engines (Splink/DuckDB, Zingg/Spark, Senzing) win here. The 2026-05-12 Round 5 audit halved the 1M wall to 12.3 min; closing this column is direction #1 of the strategy review.
3. **MCP/A2A surface is unique to us.** This is the AI-native wedge — nobody else has it because nobody else built an engine for AI agents to drive end-to-end.

---

## Tier 1 — OSS engines (the ones we benchmark against)

### 1. Splink (UK Ministry of Justice)

**Type:** OSS · MIT · pip · Python only · `moj-analytical-services/splink`
**Pricing:** Free.
**Primary approach:** Probabilistic Fellegi-Sunter (EM-trained m/u probabilities, fixed u from random pairs). Runtime is a SQL backend abstraction: DuckDB by default, Spark / Athena / Postgres available.

**Snapshot.** The most-cited modern OSS ER tool. Built by the UK Ministry of Justice for cross-agency PII matching at scale; widely adopted in UK gov and gaining traction in private sector. Strongest at PII; weaker on non-PII data shapes. Active community, frequent releases.

**Technical model.** Pure Fellegi-Sunter probabilistic record linkage. Custom comparison levels per field, EM training, automatic threshold via score histogram. The DuckDB backend means it inherits DuckDB's out-of-core story for free — Splink can comfortably score 100M+ pairs on a laptop.

**Scale & deployment.** Spark backend handles billions; DuckDB backend handles 100M+ rows on a single machine. They publicly demo 1B-row runs.

**Privacy / governance.** No PPRL out of the box. No multi-user UI. Charts and HTML reports.

**Reference data.** None bundled.

**AI / active learning.** No LLM integration. Active learning via manual labelling helpers; threshold calibration tools.

**Where Splink beats GoldenMatch:**
- Throughput: 1B+ rows demonstrated; we're 1M in 12.3 min.
- PII F1 on Febrl: 0.998 vs our 0.971. Splink is the F-S champion.
- DuckDB-native by default; we treat it as a backend option.

**Where GoldenMatch beats Splink:**
- Non-PII (DBLP-ACM zero-config 0.964 vs Splink 0.728 [^splink]). Splink's F-S model assumes the comparison-level distributions hold; on bibliographic data they don't.
- Zero-config: Splink requires a comparison specification per field; we ship a controller.
- Polyglot: Python-only vs our Py + TS + Rust + SQL.
- AI-native: no MCP, no A2A.
- LLM-augmented borderline scoring: not in Splink.
- PPRL: not in Splink.

**Composable?** Yes — MIT, well-architected Python library. Plausible to embed Splink's F-S backend as a `goldenmatch` strategy (we already have `core/probabilistic.py` with Splink-style EM; deeper integration possible).

---

### 2. dedupe (Python lib) + dedupe.io (Forest Gregg)

**Type:** OSS lib + commercial SaaS · BSD · pip · Python only · `dedupeio/dedupe`
**Pricing:** Library free. dedupe.io SaaS: from ~$1K/month for small workloads.
**Primary approach:** Active-learning-first. ML classifier on labelled pair examples + blocking via affine-gap predicates.

**Snapshot.** The granddaddy of OSS Python ER. Pioneered the "label 20 pairs and we'll learn" UX in 2012. Slower than the field on raw throughput, but the active-learning loop is the gold standard nobody has fully matched.

**Technical model.** Two-stage: blocking predicates (chosen by Beam search over a predicate space) + a logistic classifier trained on user-labelled pairs. Affine-gap string distance is the default comparator. Cluster step uses hierarchical agglomerative clustering with a cut threshold.

**Scale & deployment.** Single-machine. 10M rows is the documented practical ceiling. Slowest of the OSS tier on our benchmarks (Febrl 7.2s, DBLP-ACM 10.5s vs our 6.8s / 6.2s).

**Privacy / governance.** No PPRL. No multi-user UI in OSS; dedupe.io adds a steward queue.

**Reference data.** None bundled.

**AI / active learning.** Active learning is the headline. Confusion-matrix-prioritized pair surfacing. No LLM.

**Where dedupe beats GoldenMatch:**
- Active learning UX is the cleanest in OSS. We have it (`core/active_sampling.py`) but it's TUI/algorithm; dedupe makes it the primary onboarding flow.
- dedupe.io has a mature steward/reviewer workflow with billing-attached SaaS.

**Where GoldenMatch beats dedupe:**
- Out-of-box accuracy without labels (zero-config). dedupe requires 10+ labels minimum.
- Throughput at every scale.
- Polyglot, AI-native, PPRL.
- Reference data integration (planned).

**Composable?** Yes — BSD. We could publish a `goldenmatch[dedupe-compat]` adapter that imports a dedupe model file.

---

### 3. RecordLinkage Toolkit (Jonathan de Bruin)

**Type:** OSS · BSD · pip · Python only · `J535D165/recordlinkage`
**Pricing:** Free.
**Primary approach:** Classical record linkage. Pandas-native. Indexing (blocking), comparison, classification (rules, F-S, KMeans, logistic).

**Snapshot.** The pure-Python academic workhorse. Stable, well-documented, low-magic. Excels on small-to-medium bibliographic and academic-comparison datasets. Less actively developed than Splink.

**Technical model.** Pipeline: `Index` (block) → `Compare` (vector of similarities) → `Classify` (rule-based, F-S, KMeans, logistic regression). Pandas-centric; reads naturally for tabular data.

**Scale & deployment.** Single-machine. Pandas memory model; 1M+ is heavy.

**Privacy / governance.** No PPRL. No UI.

**Reference data.** None bundled.

**AI / active learning.** No LLM. Classifier-style ML with sklearn integration. No active-learning loop.

**Where RecordLinkage Toolkit beats GoldenMatch:**
- DBLP-ACM F1 0.923 — narrowly tops us at 0.918 (hand-tuned baseline). Our auto-config controller now pushes us to 0.964, well past.
- Cleanest pedagogy for "how does ER actually work?". The toolkit is taught in classrooms.

**Where GoldenMatch beats RecordLinkage Toolkit:**
- Every other axis: throughput, accuracy at larger scales, PPRL, AI-native, zero-config, polyglot.

**Composable?** Yes. Most likely embedding path: scorer plugins from the toolkit imported into goldenmatch.

---

### 4. Zingg / Zingg.AI (Sonal Goyal)

**Type:** OSS core (AGPL) + commercial · pip + Spark · `zinggAI/zingg`
**Pricing:** OSS free under AGPL (viral copyleft — restrictive for closed-source embedding). ZinggAI commercial pricing not public; enterprise sales motion.
**Primary approach:** Spark + ML classifier. Active learning with a CLI labeller.

**Snapshot.** Spark-native ER toolkit, Java/Scala under the hood with a Python wrapper. Built for warehouses that already live on Spark. Active learning is central. AGPL license keeps it OSS but limits commercial embedding.

**Technical model.** Active-learning classifier (gradient-boosted trees by default) on labelled pairs, with sklearn-style hyperparameter tuning. Blocking is automatic via predicate Beam search (similar to dedupe). Runs on Spark; can write back to Spark tables, JDBC, Snowflake, Databricks.

**Scale & deployment.** Spark-scale. Production-shaped Spark jobs; 100M+ row deployments documented.

**Privacy / governance.** No PPRL in OSS. ZinggAI commercial adds review queue.

**Reference data.** None bundled.

**AI / active learning.** Active learning is central. ZinggAI commercial has added LLM-assisted labelling.

**Where Zingg beats GoldenMatch:**
- Spark scale (native, not via the Ray adapter).
- Spark-shop integration: drop into existing Spark pipelines.
- Active-learning UX more polished than ours.

**Where GoldenMatch beats Zingg:**
- License: MIT vs AGPL. Embedders prefer us.
- Polyglot: Python + TypeScript + Rust + SQL vs Java/Scala-only engine.
- Single-machine throughput per dollar (Spark has minimum cluster cost).
- AI-native: MCP/A2A.
- Zero-config: Zingg requires labels; we don't.
- PPRL.

**Composable?** Awkward — AGPL forces our suite to either also be AGPL or embed via process-isolation. We won't embed; they could embed us under MIT (and probably should — we have features they lack).

---

### 5. JedAI Toolkit (NTUA, Greece)

**Type:** OSS · Apache 2.0 · Java · `scify/JedAIToolkit`
**Pricing:** Free.
**Primary approach:** Java-based academic toolkit. Schema-based + schema-agnostic ER. Heavy on blocking research (block-purging, comparison-cleaning).

**Snapshot.** The academic Java answer to RecordLinkage Toolkit. Strongest on blocking and meta-blocking research; less mature on the end-to-end UX. Used in research papers more than in production.

**Technical model.** Schema-agnostic blocking is the differentiator — token-based blocks, q-grams, meta-blocking (graph-based pruning of comparison candidates). Comparison and clustering modules are standard. CLI + GUI for desktop use.

**Scale & deployment.** Single-machine Java. Modest scale; the project is research-led.

**Where JedAI beats GoldenMatch:**
- Java shops with existing JVM tooling.
- Meta-blocking research is the deepest in OSS; we should read their papers.

**Where GoldenMatch beats JedAI:**
- Pretty much every modern axis.

**Composable?** No direct embed; could port their blocking ideas (we already implement adaptive, sorted neighbourhood, canopy).

---

### 6. py_entitymatching / Magellan (UW–Madison)

**Type:** OSS · BSD · pip · Python · `anhaidgroup/py_entitymatching`
**Pricing:** Free.
**Primary approach:** End-to-end matching pipeline as a step-by-step workflow. Strong on debugging tools; ML classifier-based.

**Snapshot.** Academic toolkit out of AnHai Doan's group at UW–Madison. Designed to teach the ER workflow. Less production-shaped than Splink or Zingg.

**Technical model.** Workflow-oriented: sample → label → train → debug → run. Each step has a Jupyter-friendly API. Classifier zoo (logistic, random forest, XGBoost). Their "ML debugging" tooling for understanding why a model misses is genuinely good.

**Scale & deployment.** Single-machine, pandas-based.

**Where Magellan beats GoldenMatch:**
- Debugging tooling for ML pipelines.
- Academic credibility.

**Where GoldenMatch beats Magellan:**
- Production-shaped: dedupe.io-style API, persistent corrections, golden records.
- Throughput, polyglot, AI-native, zero-config.

**Composable?** No direct embed planned; debugging-tool ideas worth lifting.

---

### 7. fuzzymatcher + primitives (rapidfuzz, jellyfish, libpostal)

**Type:** OSS primitives · MIT/BSD · pip
**Pricing:** Free.

**Snapshot.** Not engines — primitives. Listed here because they're often what people *think* of as "I'll just dedupe with rapidfuzz". GoldenMatch already depends on rapidfuzz and jellyfish; libpostal is on the reference-data roadmap.

**Where they beat GoldenMatch:** simpler for a 10-line script.

**Where GoldenMatch beats them:** anything beyond a 10-line script. Blocking, clustering, golden records, calibration, explainability, persistence — they don't exist at this layer.

**Composable?** We *do* compose them. They're in our dependency tree.

---

### 8. Senzing G2 Community Edition

**Type:** Semi-OSS · Apache 2.0 wrapper around closed core · `Senzing/G2Module`
**Pricing:** Community edition: free up to 100K records. Enterprise: commercial license, ~$50K–$500K+ ARR.
**Primary approach:** Deterministic-first identity-graph ER. C/C++ core, thin language bindings.

**Snapshot.** The "we did this for 25 years" engine. Built specifically for person/business identity resolution with 20+ years of curated reference data and deterministic rules. Community edition exposes the engine; the *moat* is the reference data + rule library that ships with Enterprise. Real-time, low-latency by design.

**Technical model.** Deterministic-first: rule-based matching with name dictionaries, address normalizers, watchlist hits. Probabilistic and graph layers on top. Engine emits an identity graph natively — records become entity nodes with edge provenance. Real-time API (sub-50ms typical match-one).

**Scale & deployment.** 10M+ records on a single node, default. 100M+ with sharding. Real-time match-one is core, not a bolt-on.

**Privacy / governance.** Hashed-attribute matching available but not as flexible as goldenmatch PPRL.

**Reference data.** This is the moat. Bundled: 100+ ethnic name dictionaries, business legal-form normalization, watchlist (OFAC, World-Check integrations), 200+ country address formats. **The single largest reason customers choose Senzing.**

**AI / active learning.** Limited ML; the engine is deterministic by design. No LLM integration (and they market this as a *feature* — auditable decisions for KYC/AML compliance).

**Where Senzing CE beats GoldenMatch:**
- Identity graph as native output.
- Reference data (the headline).
- Real-time match-one latency by design.
- 25-year operational history; trusted by US Census, large banks, intel.
- Auditable: deterministic decisions are easier to defend in regulated industries.

**Where GoldenMatch beats Senzing CE:**
- OSS license vs closed core (CE wraps a closed brain).
- Polyglot: Senzing has Python/Java/Go bindings to a C library; we have native impls.
- Zero-config controller: Senzing requires schema mapping and rule selection.
- AI-native: MCP, A2A.
- LLM-augmented borderline scoring.
- Bibliographic / product matching (Senzing is people/business focused).
- PPRL.
- Modern dev experience (pip install vs binary distribution + license keys).

**Composable?** Limited. The closed core means we can't extend Senzing; they can't embed us either (license conflict). Realistic posture: customers use Senzing for the people-and-business identity domain and GoldenMatch for everything else. We should publish a "GoldenMatch as Senzing complement" pattern doc.

---

## Tier 2 — identity-graph engines

### 9. Senzing G2 Enterprise

See above. Enterprise edition adds reference data, support, multi-tenant deployment, real-time HTTP API. **The closest single competitor to where direction #2 of our strategy review points.** Strategy posture: don't try to win on reference data; win on AI-native + composability + price.

---

### 10. Quantexa Decision Intelligence

**Type:** Commercial · proprietary · UK
**Pricing:** Enterprise — $500K–$5M+ ARR typical. Six-month deployments.
**Primary approach:** Graph-native ER + contextual decisioning. Built for fraud, AML, KYC.

**Snapshot.** The Tier-1 graph ER vendor for finance and government. Acquired Innovation Capital, raised $129M Series E in 2023, late-stage. Their engine emits a contextualized identity + relationship graph, not a clusters dict. Strongest where ER feeds downstream fraud/AML/KYC decisioning.

**Technical model.** Proprietary graph store + their own ER engine + a network-based decisioning layer. Records become entities with relationship edges, and downstream models reason over the graph (e.g., "this transaction is suspicious because the recipient is 2 hops from a sanctioned entity"). Heavily services-led.

**Scale & deployment.** Billions of records, enterprise on-prem or VPC.

**Where Quantexa beats GoldenMatch:**
- Graph-native engine output.
- Decisioning layer on top (fraud / AML / KYC).
- Enterprise-grade governance, audit, RBAC.
- Reference data + watchlist integrations.

**Where GoldenMatch beats Quantexa:**
- OSS + composable. Quantexa is closed and services-led.
- Time-to-value: pip install vs 6-month engagement.
- AI-native (MCP / A2A).
- Polyglot.
- Cost: ~$0/year vs $500K+.

**Composable?** No. They're a platform play; we're an engine play. Different segments of buyer.

---

### 11. Tilores

**Type:** Commercial · proprietary · API-as-a-service · Germany
**Pricing:** Usage-based, ~$0.001/record matched. Free tier under 10K/month.
**Primary approach:** Identity-graph-as-a-service with GraphQL API.

**Snapshot.** Startup-stage identity graph SaaS. Strong API-design ethos: GraphQL surface over a managed identity graph; you POST records and query the resulting graph. Real-time match-one is the headline. Smaller than Senzing/Quantexa but architecturally newer.

**Technical model.** Closed engine, GraphQL surface. Async-friendly. Multi-source merge with source-priority rules. Edge provenance per relationship.

**Scale & deployment.** Managed SaaS; their problem, not yours.

**Where Tilores beats GoldenMatch:**
- Identity-graph engine output.
- GraphQL API is well-designed.
- Hosted-by-default; we're install-by-default.
- Real-time match-one < 100ms p95.

**Where GoldenMatch beats Tilores:**
- OSS — you can run us anywhere.
- Polyglot edge runtimes (Cloudflare Workers, Vercel Edge).
- Zero-config.
- AI-native (MCP / A2A).
- LLM-augmented scoring.
- PPRL.

**Composable?** No. They're SaaS; we're OSS. Buyers choose based on hosting preference, not features.

---

### 12. LexisNexis Risk Solutions (Accurint, TrueDepth)

**Type:** Commercial · proprietary · US
**Pricing:** Enterprise, opaque. Annual subscriptions, often per-query.
**Primary approach:** Identity graph + curated US data sources (court records, credit headers, address history).

**Snapshot.** The default US identity-verification vendor for finance and government. Strongest in regulated US verticals. Their *data* is the moat, not the engine.

**Where LexisNexis beats GoldenMatch:**
- US identity data depth — court records, credit headers, address history — that nobody else has legal/operational access to.
- Regulated-industry trust (KYC, BSA/AML).

**Where GoldenMatch beats LexisNexis:**
- Anywhere the buyer isn't doing US identity verification.
- OSS + composable.
- AI-native.
- Polyglot.

**Composable?** No — different category. They're a data company that happens to do ER. We're an engine.

---

## Tier 3 — cloud-managed ER

### 13. AWS Entity Resolution

**Type:** Managed service · proprietary · AWS-only
**Pricing:** Per matching record. ~$0.5/1K to $2/1K depending on tier (publicly listed).
**Primary approach:** Managed ML-based + rule-based ER. Integrates with Glue, S3, Redshift, Lake Formation.

**Snapshot.** AWS's bid for the managed-ER category. Launched 2023, broadly available 2024. Three matching workflows: rule-based, ML-based, data-service-provider (LiveRamp). The pitch is "scale is not your problem".

**Technical model.** Three workflows — rule-based (you author the rules), ML-based (you label, AWS trains), provider-based (you query LiveRamp's identity graph). Tied to Glue Data Catalog for schema; runs in your AWS account but their service plane.

**Scale & deployment.** AWS-scale, hidden behind a managed surface.

**Privacy / governance.** Inherits AWS account controls. Data residency: pick your region. No PPRL.

**Where AWS ER beats GoldenMatch:**
- "Scale is AWS's problem". No infrastructure choices.
- Glue / Lake Formation integration is one-click.
- LiveRamp data-provider workflow.

**Where GoldenMatch beats AWS ER:**
- Portability: same engine on a laptop, in Postgres, in an edge worker.
- Accuracy on the workflows that aren't well-suited to AWS's three modes.
- OSS + zero-config.
- AI-native (MCP / A2A).
- Multi-cloud.
- PPRL.

**Composable?** No. AWS ER is a service; we're a library. Customers should be able to compare: "AWS ER for AWS-native pipelines, GoldenMatch for everything else".

---

### 14. Google Cloud Entity Reconciliation / Cloud Data Catalog ER

**Type:** Managed service · proprietary · GCP-only
**Pricing:** Tied to Knowledge Graph & Cloud Data Catalog usage.
**Primary approach:** Knowledge-Graph-anchored ER + ML on Vertex AI.

**Snapshot.** GCP's answer, integrated with their Knowledge Graph. Less mature than AWS ER as a product offering.

**Where GCP beats GoldenMatch:** Vertex AI integration; Google Knowledge Graph anchoring (entities resolve to public KG IDs).

**Where GoldenMatch beats GCP:** portability, OSS, AI-native (MCP), zero-config, PPRL, polyglot.

**Composable?** No.

---

### 15. Snowflake Cortex (Match / AI Functions)

**Type:** Managed warehouse functions · proprietary · Snowflake-only
**Pricing:** Snowflake compute credits.
**Primary approach:** SQL UDFs for fuzzy match + embedding-based similarity inside Snowflake.

**Snapshot.** Snowflake's first-party fuzzy + embedding match UDFs (`SNOWFLAKE.CORTEX.AI_SIMILARITY`, `CORTEX.AI_CLASSIFY`). Not a full ER engine — primitives that compose into a SQL ER pipeline.

**Where Snowflake beats GoldenMatch:**
- Run in-warehouse, no data movement.
- Snowflake-buyer default.

**Where GoldenMatch beats Snowflake:**
- A full engine: blocking, clustering, golden records, calibration, persistence — Snowflake ships primitives, not workflows.
- Portable across warehouses.
- Accuracy: published F1 numbers on standard benchmarks.

**Composable?** Yes — we already ship a Snowflake connector. A Snowflake Native App for GoldenMatch is a known direction (in the strategy review's golden-showcase scope).

---

### 16. Databricks ARC

**Type:** Apache 2.0 · `databricks-industry-solutions/auto-data-linkage`
**Pricing:** Library free; you pay for Databricks runtime.
**Primary approach:** Splink-based, automated configuration for Spark.

**Snapshot.** Databricks' answer: a thin productized wrapper around Splink with Databricks-shaped defaults. Free, OSS-flavored, Databricks-targeted.

**Where ARC beats GoldenMatch:** Databricks-native; Splink-power for those who want it.

**Where GoldenMatch beats ARC:** zero-config breadth (ARC is Splink-shaped), non-PII accuracy, AI-native, polyglot, PPRL, identity-graph (planned), portable.

**Composable?** Yes — both OSS. Conceivably embed ARC's autoconfig ideas into our Splink-strategy path.

---

## Tier 4 — enterprise MDM (the money tier)

These are not direct engine competitors — they're MDM **platforms** that include an ER engine. Their moat is workflow, governance, services, and reference data, not the matcher. Our strategy is: don't compete with the platform; be the engine inside or beside it.

### 17. Tamr

**Type:** Commercial · proprietary · US
**Pricing:** $250K–$2M ARR typical. 6-month deployments.
**Primary approach:** ML-driven ER with human-in-the-loop steward workflow. Their original differentiator was active-learning ML at enterprise scale.

**Where Tamr beats GoldenMatch:** enterprise steward workflows, services-led deployment, customer references in F500.

**Where GoldenMatch beats Tamr:** time-to-value (pip install vs 6 months), price (free vs $500K+), AI-native, OSS, polyglot.

**Composable?** No — they're a closed platform. Their engine could plausibly be replaced by ours if a customer wanted to escape; that's a migration narrative worth writing.

---

### 18. Reltio

**Type:** Commercial · proprietary cloud MDM · US
**Pricing:** $400K+ ARR typical. Salesforce-shop default.
**Primary approach:** Cloud MDM with built-in survivorship, hierarchy, workflow. ER engine is one component.

**Where Reltio beats GoldenMatch:** multi-domain MDM (customer + product + supplier + asset), entity hierarchies, workflow engine, Salesforce-native.

**Where GoldenMatch beats Reltio:** price (100× cheaper), pip install vs 6-month implementation, AI-native, OSS, deeper engine accuracy on specific benchmarks.

---

### 19. Informatica MDM / IDQ

**Type:** Commercial · proprietary · US
**Pricing:** $250K+ ARR; on-prem + cloud editions.
**Primary approach:** Incumbent MDM, ML-augmented (CLAIRE). Trusted by F500.

**Where Informatica beats GoldenMatch:** F500 procurement defaults, depth of data-governance suite, deployed in regulated industries for 20+ years.

**Where GoldenMatch beats Informatica:** modernity. Their engine is a generation behind Splink/Zingg/us. Time-to-value, AI-native, OSS, polyglot.

---

### 20. Ataccama ONE

**Type:** Commercial · proprietary · Czech
**Pricing:** $100K–$500K ARR.
**Primary approach:** Unified data quality + MDM + observability platform with ER inside.

**Where Ataccama beats GoldenMatch:** integrated DQ + MDM; data observability stack.

**Where GoldenMatch beats Ataccama:** engine accuracy, AI-native, OSS, polyglot. Their ER component is competent but not their differentiator.

---

### 21. Stibo Systems STEP

**Type:** Commercial · proprietary · Denmark
**Pricing:** $250K+ ARR typical.
**Primary approach:** Product / supplier MDM with ER component. Strong in retail and manufacturing.

**Where Stibo beats GoldenMatch:** product MDM workflows (PIM), retail / manufacturing vertical defaults.

**Where GoldenMatch beats Stibo:** the ER engine itself. Time-to-value, AI-native, OSS.

---

### 22. Profisee

**Type:** Commercial · proprietary · US
**Pricing:** $100K–$300K ARR.
**Primary approach:** Mid-market MDM, Microsoft-stack-native.

**Where Profisee beats GoldenMatch:** Microsoft-shop integration (Azure, Dynamics).

**Where GoldenMatch beats Profisee:** engine accuracy, AI-native, polyglot, OSS.

---

## Tier 5 — research SOTA

### 23. Ditto (Megagon Labs)

**Type:** OSS research · MIT · `megagonlabs/ditto`
**Pricing:** Free.
**Primary approach:** Fine-tuned BERT/RoBERTa transformers for pair classification.

**Snapshot.** The published F1-leader on product-matching benchmarks. A pre-trained transformer fine-tuned on labelled pairs. Research-grade — not production-shaped.

**Technical model.** Serialize record pair as a text sequence ("col1: val1 col2: val2 [SEP] col1: val1' col2: val2'"), fine-tune BERT to predict match/no-match. Augmentations (entity-level swaps, dropout) boost generalization.

**Scale & deployment.** Research code; not designed for production throughput. Single-machine inference.

**Where Ditto beats GoldenMatch:**
- Abt-Buy F1 0.893 vs our 0.722 (+LLM) / 0.817 (+Vertex AI). The product-matching SOTA.
- Amazon-Google F1 0.92+ with 1000+ labels.

**Where GoldenMatch beats Ditto:**
- Production-shaped: pip install, CLI, REST, MCP, A2A, golden records, persistence.
- Zero training labels needed.
- Non-product domains (PII, bibliographic).
- Throughput, polyglot, AI-native (MCP).
- LLM-augmented scoring is cheaper than fine-tuning a transformer.

**Composable?** Yes — Ditto-trained models could be a GoldenMatch scorer plugin. Direction #4 of the strategy review (LLM ↔ distilled-classifier) covers this neighborhood with cheaper economics.

---

## Tier 6 — adjacent (not really competitors)

Listed because customers sometimes ask "but what about X?". Short entries.

### dbt / dbt Cloud
**Not an ER engine.** Transformation framework. We ship `dbt-goldencheck`; a `dbt-goldenmatch` is planned. dbt is a partner, not a competitor.

### Monte Carlo / Soda / Bigeye
**Data observability**, not ER. They tell you when your pipeline broke; we tell you which records are the same.

### Hightouch / Census
**Reverse ETL.** They sync data from warehouse to SaaS apps. We deduplicate it first.

### Stitch / Fivetran / Airbyte
**Ingestion.** They get data into the warehouse. We resolve entities once it's there.

### Apache Hudi / Iceberg / Delta
**Table formats.** Storage layer; orthogonal.

### MLflow / Weights & Biases
**ML experiment tracking.** Useful if you fine-tune a Ditto-class model; we don't require it.

### LiveRamp
**Identity-data provider**, not engine. Sells the third-party identity graph. Could be a data input to GoldenMatch via the AWS ER data-provider workflow.

---

## Cross-cutting observations

### Pattern 1 — the "OSS engines + closed reference data" sandwich

Senzing, LexisNexis, Quantexa, Tilores all sell **data + engine** as one package. OSS engines (Splink, dedupe, Zingg, GoldenMatch) sell engine only. The buyer choice is: build your own reference data on top of OSS, or pay for an integrated bundle. **GoldenMatch's path is to make bundling OSS reference data (libpostal, US Census, OpenCorporates, NAICS) into the engine a one-line install** (strategy direction #8).

### Pattern 2 — the "managed warehouse" wave

AWS Entity Resolution, Snowflake Cortex Match, Databricks ARC, GCP Entity Reconciliation all bet that buyers want ER inside their warehouse. The bet is right for warehouse-native pipelines; **the bet is wrong for everyone who needs the same engine in their app code, edge worker, or Postgres trigger.** GoldenMatch's portability story is the wedge here.

### Pattern 3 — zero-config is uncontested

Going axis-by-axis across this matrix, **no other vendor publishes zero-config benchmark numbers**. Splink's docs explicitly require a comparison-level specification. Dedupe requires labels. Tamr/Reltio sell services to do the configuration. AWS ER asks you to pick a workflow. **The introspective auto-config controller is GoldenMatch's most defensible engine moat.** It will be copied within 12–24 months if we don't keep extending it (strategy direction #3).

### Pattern 4 — identity-graph engines are the next frontier

Senzing, Quantexa, Tilores all *emit a graph*, not a clusters dict. The mainstream OSS engines (Splink, dedupe, RecLink Toolkit, Zingg, GoldenMatch v1.9) all emit clusters. **Strategy direction #2 (identity graph as first-class engine output) is the bet that this gap will be the defining one of 2027.** Senzing's 25 years of head-start on reference data can't be matched, but their 5-year head-start on the graph output can.

### Pattern 5 — AI-native is uncontested but unproven

Nobody else ships MCP, A2A, or LLM-budget-controlled borderline scoring. We're alone here. The risk: this matters in 2026 if "AI agents drive ER pipelines" becomes a real pattern, and it's noise if it doesn't. Bet: it becomes real. **Hedge: keep the engine fully usable without any LLM** (strategy direction #4: hybrid distilled-classifier scorer).

### Pattern 6 — license matters more than buyers admit

Splink (MIT) and dedupe (BSD) have outgrown Zingg (AGPL) and Senzing (closed core) partly on license. AGPL specifically blocks embedding by other OSS tools. **GoldenMatch's MIT is a long-term moat that's invisible day to day.** Don't change it.

---

## Caveats

1. **Pricing is indicative.** Most enterprise vendors don't publish list prices. Numbers above are 2024–2026 industry estimates based on public case studies, RFP responses, and analyst notes. Treat ranges as orders of magnitude, not quotes.
2. **Vendor product surfaces change quickly.** AWS ER and Snowflake Cortex shipped major releases between 2023 and 2026; their capabilities matrix may be more competitive than this doc reflects. Last full re-survey: 2026-05.
3. **Our own numbers are reproducible.** Every GoldenMatch claim in this doc points back to `docs/reproducing-benchmarks.md`, `docs/scale-audit-2026-05.md`, or `packages/python/goldenmatch/CHANGELOG.md`. If you can't reproduce one, it's a bug — file it.
4. **The "where vendor X beats us" entries are the most likely to age.** Direction #1 of the strategy review (throughput) and direction #2 (identity graph) are explicitly targeting two of the most common columns where we lose. Expect updates.
5. **Some vendors we deliberately left out.** Salesforce Data Cloud, SAP Master Data Governance, Talend Data Fabric, IBM InfoSphere QualityStage — all have ER components but are platform plays where the ER engine is a feature, not the product. The Tamr/Reltio/Informatica/Stibo/Ataccama/Profisee entries above cover the same buyer pattern; we'd be repeating ourselves.
6. **Academic benchmarks ≠ enterprise outcomes.** F1 on DBLP-ACM tells you something. F1 on a customer's product catalog with 2M rows of weird shape tells you something different. The leaderboard direction (#5) is about closing this gap.

---

## What to do with this doc

- **Sales / GTM (in golden-showcase repo):** lift the "where GoldenMatch beats X" bullets per vendor. Position relative to whichever competitor a buyer mentioned.
- **Engine roadmap (in this repo):** the cross-cutting observations are the strategic input to the directions in `docs/superpowers/specs/2026-05-08-competitive-strategy-review.md`. Patterns 3 (zero-config), 4 (identity graph), and 5 (AI-native) are the three moats; patterns 1 (reference data) and 2 (warehouse-native) are the gaps.
- **Customer conversations:** if a customer says "we're evaluating GoldenMatch vs X", look up X and lead with the "where they beat us" section. Honesty up front; lead with where you lose, then explain why the engine still wins overall.
- **PR review:** when a new feature changes a capability cell in the matrix at the top, update the matrix in the same PR. This doc should stay accurate, not aspirational.

---

## Footnotes

[^bench]: GoldenMatch numbers come from `docs/reproducing-benchmarks.md` (per-dataset runner + expected output) and the entries in `packages/python/goldenmatch/CHANGELOG.md` for v1.8 through v1.15. Verified-stamp dates accompany each row in the reproducing-benchmarks doc.

[^splink]: Splink F1 numbers (0.998 Febrl, 0.728 DBLP-ACM) come from this repo's `D:\show_case\golden-showcase\comparison_bench\` head-to-head runner — see `packages/python/goldenmatch/CLAUDE.md` reference to that directory. Numbers reflect the Splink 4.x release line; rerun if comparing against a newer Splink.

[^reclink]: RecordLinkage Toolkit F1 0.923 on DBLP-ACM from the head-to-head runner above (RecLink ~0.18.x). Author publishes example notebooks at https://recordlinkage.readthedocs.io/ with DBLP-ACM in the standard examples corpus.

[^ditto]: Ditto F1 numbers (Abt-Buy 0.893, Amazon-Google 0.92+ with 1000+ labels) come from Megagon Labs's published papers; Ditto is research code so reproduction requires fine-tuning a transformer on the published splits.
