<!-- mcp-name: io.github.benseverndev-oss/goldenmatch -->
<div align="center">

# Golden Suite

**Zero-config entity resolution that scales — dedupe & match messy records from a laptop CSV to 100M+ rows. No training data, no tuning.**

The headline package, **GoldenMatch**, does the matching — fuzzy + exact + probabilistic (Fellegi-Sunter) + LLM — and **beats hand-tuned Splink out of the box** (96.4% F1 on DBLP-ACM), identical in Python, edge-safe TypeScript, and SQL. It even runs on **unstructured input**: extract records from PDFs and images, then dedupe. Around it sits a full data-quality suite — Check, Flow, Analysis, Pipe, InferMap — with a Rust layer for Postgres / DuckDB and optional WebAssembly acceleration behind the TS ports.

**Made for GraphRAG, too** — entity resolution is the stage knowledge-graph pipelines do *worst* (the same entity scatters across documents as duplicate surface forms). GoldenMatch drops into **neo4j-graphrag / LlamaIndex / Graphiti** as the resolution stage ([`goldenmatch-kg`](packages/python/goldenmatch-kg/README.md)), or builds a KG straight from text with that resolution at its core ([`goldengraph`](packages/python/goldengraph/README.md)). [→ Knowledge graphs](#knowledge-graphs)

**Verified at scale: 100,000,000 records deduped in 9.2 min on a Ray cluster — recall-complete across any partitioning, 0.36 GB driver footprint.**

<br>

<!-- Headline package: goldenmatch -->
[![PyPI — goldenmatch](https://img.shields.io/pypi/v/goldenmatch?color=d4a017&label=pypi%3Agoldenmatch&logo=pypi&logoColor=white)](https://pypi.org/project/goldenmatch/)
[![npm — goldenmatch](https://img.shields.io/npm/v/goldenmatch?color=cb3837&label=npm%3Agoldenmatch&logo=npm&logoColor=white)](https://www.npmjs.com/package/goldenmatch)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776ab?logo=python&logoColor=white)](https://python.org)
[![Node](https://img.shields.io/badge/node-%3E%3D20-5fa04e?logo=nodedotjs&logoColor=white)](https://nodejs.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

<!-- Quality / proof -->
[![CI](https://github.com/benseverndev-oss/goldenmatch/actions/workflows/ci.yml/badge.svg)](https://github.com/benseverndev-oss/goldenmatch/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/benseverndev-oss/goldenmatch/graph/badge.svg)](https://codecov.io/gh/benseverndev-oss/goldenmatch)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/benseverndev-oss/goldenmatch/badge)](https://scorecard.dev/viewer/?uri=github.com/benseverndev-oss/goldenmatch)
[![Fellegi-Sunter beats hand-rolled Splink](https://img.shields.io/badge/Fellegi--Sunter-beats%20hand--rolled%20Splink-d4a017)](docs/benchmarks/2026-06-09-splink-bakeoff.md)
[![DBLP-ACM F1](https://img.shields.io/badge/DBLP--ACM%20F1-96.4%25-d4a017)](packages/python/goldenmatch/README.md#benchmarks)

<!-- Reach -->
[![PyPI downloads (suite)](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fbenseverndev-oss%2Fgoldenmatch%2Fbadges%2Fpypi-downloads.json)](https://pepy.tech/projects?q=goldenmatch+goldencheck+goldenpipe+goldenflow+goldenanalysis+infermap+goldencheck-types+goldensuite-mcp+goldenmatch-duckdb+goldenmatch-native+goldenflow-native+goldencheck-native+goldenanalysis-native+goldengraph-native+goldenmatch-embed+golden-suite)
[![npm downloads (suite)](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fbenseverndev-oss%2Fgoldenmatch%2Fbadges%2Fnpm-downloads.json)](https://www.npmjs.com/~benzsevern)
[![GitHub stars](https://img.shields.io/github/stars/benseverndev-oss/goldenmatch?style=flat&color=d4a017&logo=github)](https://github.com/benseverndev-oss/goldenmatch/stargazers)

<!-- Ecosystem -->
[![Docs](https://img.shields.io/badge/docs-docs.bensevern.dev-d4a017)](https://docs.bensevern.dev/)
[![Wiki](https://img.shields.io/badge/wiki-github-d4a017)](https://github.com/benseverndev-oss/goldenmatch/wiki)
[![Web UI](https://img.shields.io/badge/web%20ui-FastAPI%20%2B%20React-d4a017?logo=react&logoColor=white)](https://github.com/benseverndev-oss/goldenmatch/wiki/Web-UI)
[![Smithery MCP](https://img.shields.io/badge/MCP-smithery-6e40c9)](https://smithery.ai/servers/benzsevern/goldenmatch)
[![Last commit](https://img.shields.io/github/last-commit/benseverndev-oss/goldenmatch?color=d4a017&label=last%20commit)](https://github.com/benseverndev-oss/goldenmatch/commits/main)

</div>

[![GoldenMatch web workbench — pair drilldown with NL prose](packages/python/goldenmatch/docs/screenshots/web/web-inspector.png)](https://github.com/benseverndev-oss/goldenmatch/wiki/Web-UI)

<p align="center"><sub><em>Pair drilldown in the web workbench: cluster members, field-level diff, and a one-line NL explanation per pair. <code>pip install goldenmatch[web]</code> then <code>goldenmatch serve-ui &lt;project&gt;</code>. <a href="https://github.com/benseverndev-oss/goldenmatch/wiki/Web-UI">More screenshots →</a></em></sub></p>

```bash
# Dedupe a CSV in 30 seconds — zero config, writes <timestamp>_golden.csv.
# Add --tui to review interactively, --output-all for every artifact.
pip install goldenmatch && goldenmatch dedupe customers.csv

# From Python — zero-config, returns golden records
python -c "import goldenmatch as gm; gm.dedupe('customers.csv').golden.write_csv('deduped.csv')"

npm install goldenmatch     # TypeScript / edge runtimes
pip install golden-suite    # the WHOLE suite (Check + Flow + Match + Analysis + Pipe + InferMap) + native
```

<!-- README-callouts:start  (auto-synced from packages/python/goldenmatch/CHANGELOG.md by scripts/sync_readme_callouts.py — edit the CHANGELOG, not this block) -->
> **v3.5.0** — **New `date` scorer for date fields (#1858).** `jaro_winkler` scores unrelated
ISO birthdays 0.80+ (the fixed `YYYY-MM-DD` shape + shared digit alphabet
dominate), so it can't tell a typo from a different person. The `date` scorer
compares dates by Damerau-Levenshtein over the canonical digits — a typo scores
0.90, an unrelated date 0.00 — with a `levenshtein` fallback for non-ISO input.
Cross-surface (Python, native kernel, TypeScript), and a preflight check warns
when a name-oriented scorer sits on a date field.
>
> **v3.4.0** — **Embeddings are first-class on Fellegi-Sunter matchkeys.** `embedding` and
`record_embedding` field scorers now train (EM) and score end-to-end on the
probabilistic path via the vectorized matrix — previously they raised
`Unknown scorer` on both training and scoring. They are matrix-only, so a
matchkey carrying one always runs vectorized, and the TUI now routes FS through
the same native/vectorized selector.
>
> **v3.3.0** — **3.3.0 — negative evidence on Fellegi-Sunter matchkeys.** `negative_evidence`
now works on `type: probabilistic` matchkeys as EM-learned `__ne__` dimensions
(no labels needed; `penalty_bits` as a fixed override), and the Splink
migration upgrade pass gains a **fan-out lever** — a risk-gated NE suggestion
plus cluster-guard tuning from your reference clusters. `goldenmatch-native`
0.1.15 scores NE in the Rust kernels (`FS_SUPPORTS_NE`; older wheels keep the
pure-Python fallback automatically).
<!-- README-callouts:end -->

---

## Why a suite?

Each tool stands alone, but they compose into a single pipeline:

```mermaid
flowchart LR
    raw([raw rows])
    golden([golden records])

    subgraph orchestration ["GoldenPipe orchestrates"]
        direction LR
        infermap[InferMap]
        goldencheck[GoldenCheck]
        goldenflow[GoldenFlow]
        goldenmatch[GoldenMatch]
        infermap --> goldencheck --> goldenflow --> goldenmatch
    end

    raw --> infermap
    goldenmatch --> golden
```

| Step | Role |
|---|---|
| **InferMap** | schema mapping — auto-aligns columns across heterogeneous sources |
| **GoldenCheck** | profile + validate — encoding, format, anomaly detection |
| **GoldenFlow** | standardize + transform — phone, date, address, categorical normalization |
| **GoldenMatch** | dedupe + cluster + survivorship — fuzzy / exact / probabilistic / LLM |
| **GoldenAnalysis** | analysis + reporting — one exportable report over any stage, plus cross-run regression detection |
| **GoldenPipe** | orchestrator — declarative YAML pipeline wiring the steps |

What sets it apart:

- **Zero-config that beats hand-tuned.** 96.4% F1 on DBLP-ACM out of the box; the opt-in Fellegi-Sunter engine beats expert-tuned Splink head-to-head on every dataset Splink scores (`historical_50k` pairwise F1 **0.778 vs 0.757**, cluster B³ **0.844 vs 0.789**; one shared evaluator, [reproducible bake-off](docs/benchmarks/2026-06-09-splink-bakeoff.md)). Every step self-verifies (preflight + postflight) and returns an inspectable report instead of failing silently.
- **A healing loop, not a one-shot.** Zero-config gets you most of the way; the healer attaches ranked, self-verified config tweaks and closes the gap to expert-tuned without you being the expert. [↓ details](#the-healing-loop)
- **Durable identity.** Learning Memory persists corrections across runs (re-anchored across row reorders); the Identity Graph gives stable `entity_id`s that survive re-runs, an append-only event log, and create / absorb / merge / split semantics on CLI, REST, MCP, and SQL.
- **Privacy-preserving record linkage** — match across organizations without sharing raw data (PPRL, 92.4% F1 on FEBRL4).
- **AI-native by design** — every package ships an MCP server, a REST API, and an A2A agent surface (70+ MCP tools across the suite), all exposing the *same* JSON telemetry shape across web, TUI, CLI, Postgres, DuckDB, and MCP.
- **Polyglot parity, edge-safe, optional native speed.** The full suite ships on **npm** alongside PyPI; Python and TypeScript track the same outputs to 4-decimal precision. The TS cores are dependency-free and `node:*`-free (browsers, Cloudflare Workers, Vercel Edge, Deno); an opt-in WebAssembly backend (`await enableWasm()`) swaps in the *same* pyo3-free Rust kernels the Python wheels and SQL UDFs use, with pure-TS as the byte-identical default.
- **SQL-native at parity** — the same functions run inside **PostgreSQL** (pgrx) and **DuckDB**: dedupe / match / score / auto-config + telemetry / identity graph, profiling, `evaluate`, Fellegi-Sunter scoring, and GoldenFlow transforms.
- **Production paths** — Postgres sync, daemon mode, lineage tracking, review queues, dbt integration, GitHub Actions.

---

## Cross-language interoperability (know the limits)

The Python and TypeScript ports are at **surface parity** — the same operations
exist in both. That is *not* the same as being able to hand any pipeline phase
from one language to the other and back byte-for-byte. Some boundaries genuinely
round-trip; others are numerically tolerance-bounded; a few can't cross at all.
Each verdict below is **measured** by a conformance harness, not assumed:

| Boundary | Verdict |
| --- | --- |
| **Identity graph DB** | ✅ byte-safe + cryptographically cross-verifiable (a seal written by one toolkit validates under the other) |
| **`score → cluster`** and the **end-to-end split-run** (score in Python, cluster in TS) | ✅ byte-safe — reproduces the single-language run |
| Cluster JSON · config YAML · Learning Memory · run log · `record_fingerprint` | ✅ portable |
| **String scoring** | 🟡 4-decimal tolerance — a pair on a threshold can flip (byte-identical only with the shared WASM scorer) |
| **Standardize / dates** · embeddings · auto-config controller | 🟠 divergent — not byte-portable |
| Distributed / Ray · document (VLM) ingest · distributed routing | ⛔ Python-only by architecture |

**Rule of thumb:** hand off at the **cluster** or **identity** boundary and it's
seamless; don't split a pipeline across `standardize`/dates, embeddings, or the
controller and expect bit-exact reproduction. Full detail, guidance, and the
runnable harness that keeps these verdicts honest:
[Cross-language parity & phase-handoff limits](https://docs.bensevern.dev/concepts/cross-language-parity).

---

## The healing loop

GoldenMatch's core workflow is a loop, not a one-shot:

1. **Zero-config first pass** — `dedupe_df(df)` runs with no rules and no training data; auto-config picks a defensible config and you get good results immediately.
2. **You get the config it chose** — on `result.config`: inspectable, diffable, versionable. Never a black box.
3. **The healer suggests tweaks** — every run checks a free signal and, when there's headroom, attaches ranked, explainable, self-verified edits to `result.suggestions`. Each is kept only if it doesn't worsen an unsupervised health proxy, so a tweak never makes results worse.
4. **You apply them** — `dedupe_df(df, heal=True)` applies and re-runs in one call (returning the healed `result.config` + a `result.heal_trail`); or take the wheel with `apply_suggestion`.
5. **Results improve. Repeat** — until the healer goes quiet.

> **Wired into the default pipeline on every surface** — Python (`suggest=True` / `heal=True` / `review_config`), CLI (`--suggest` / `--heal`), MCP & A2A, REST, web, TUI, and the edge-safe TypeScript port via WebAssembly (`enableSuggestWasm()`). Needs `goldenmatch[native]`; degrades gracefully without it (attaches nothing, never errors). Kill-switch `GOLDENMATCH_SUGGEST_ON_DEDUPE=0`. Full details: [config-suggestions](https://docs.bensevern.dev/goldenmatch/config-suggestions).

---

## The Suite

| Package | Lang | What it does | Install |
|---|---|---|---|
| **[golden-suite](packages/python/golden-suite/README.md)** | Python | One-line meta-install: the whole suite + native acceleration, defaulted to the perf-optimized config. | `pip install golden-suite` |
| **[GoldenMatch](packages/python/goldenmatch/README.md)** | Python · TS | Zero-config entity resolution. Fuzzy + exact + probabilistic + LLM. Headline package. | `pip install goldenmatch` · `npm i goldenmatch` |
| **[GoldenCheck](packages/python/goldencheck/README.md)** | Python · TS | Data-quality scanning: encoding, Unicode, format validation, anomaly detection. | `pip install goldencheck` · `npm i goldencheck` |
| **[GoldenFlow](packages/python/goldenflow/README.md)** | Python · TS | Transforms & standardizers: phone, date, address, categorical normalization. | `pip install goldenflow` · `npm i goldenflow` |
| **[GoldenPipe](packages/python/goldenpipe/README.md)** | Python · TS | Orchestrator wiring Check → Flow → Match → Identity → Analysis into one declarative pipeline. | `pip install goldenpipe` · `npm i goldenpipe` |
| **[InferMap](packages/python/infermap/README.md)** | Python · TS | Schema mapping — auto-aligns columns across heterogeneous sources. | `pip install infermap` · `npm i infermap` |
| **[GoldenAnalysis](packages/python/goldenanalysis/README.md)** | Python · TS | Cross-cutting analysis & reporting — any stage's artifacts (or a raw DataFrame) → a unified exportable `AnalysisReport`; optional Rust / WASM kernels. | `pip install goldenanalysis` · `npm i goldenanalysis` |
| **[goldenmatch-extensions](packages/rust/extensions/README.md)** | Rust | Postgres extension (pgrx) + DuckDB UDFs. SQL-native fuzzy matching. | source build |
| **[dbt-goldensuite](packages/dbt/goldensuite/README.md)** | dbt · Python | dbt package — dedupe + match materializations (incl. zero-config FS), an ER build gate, quality tests, transforms, identity-graph reads. | `packages.yml` (git subdir) |
| **[goldencheck-action](packages/actions/goldencheck/README.md)** | YAML | GitHub Action — fail PRs that introduce data-quality regressions. | Marketplace |

> The deepest docs live in **[packages/python/goldenmatch/README.md](packages/python/goldenmatch/README.md)** (~1,300 lines: full feature list, CLI, architecture, benchmarks).

### Knowledge graphs

Entity resolution is the stage most GraphRAG pipelines do badly — duplicate surface forms of the same entity scatter across documents. Two packages put GoldenMatch's resolution there:

| Package | What it does | Status |
|---|---|---|
| **[goldenmatch-kg](packages/python/goldenmatch-kg/README.md)** | Drop-in GoldenMatch resolution as the ER stage of existing KG frameworks (neo4j-graphrag, LlamaIndex PropertyGraphIndex, Graphiti). One framework-agnostic `resolve_entities` core + per-framework adapters. Lift measured by [ER-KG-Bench](packages/python/goldenmatch/benchmarks/er-kg-bench), not asserted. | in-repo · first PyPI release pending |
| **[goldengraph](packages/python/goldengraph/README.md)** | Build-your-own-KG from text — `text → LLM extraction → GoldenMatch resolution → a durable bi-temporal store`. Engine (store / query / community detection) is pyo3-free Rust; ER is the differentiator. | in-repo · first PyPI release pending |

**Measured, not asserted** ([ER-KG-Bench](packages/python/goldenmatch/benchmarks/er-kg-bench)): resolution scores **F1 0.602** on the labelled set, ahead of Neo4j-KGBuilder (0.456), neo4j-graphrag (0.403), and MS-GraphRAG / LightRAG / Cognee / mem0 (0.066). Beyond ER quality, a resolved graph does two things a passage-window RAG structurally can't — **exact aggregation** (size-invariant where RAG recall collapses `0.99 → 0.64` as the answer set grows) and **temporal as-of** (`1.000` vs RAG's `0.002` on past-date queries) — both benchmarked on real Wikidata data. What it does *not* differentiate on is multi-hop QA, where a hybrid graph converges to plain text-RAG; the edge is ER + structured queries, and that's what the board measures.

---

## Real-world pipelines

Reproducible end-to-end pipelines running GoldenMatch on public data at scale, each with measured headline numbers vs baselines:

- **[shell-company-network](https://github.com/benseverndev-oss/goldenmatch-shell-company-network)** — investigative ER across ICIJ Offshore Leaks + OpenSanctions + GLEIF + UK PSC + disqualified-directors. **−62.5% analyst-hours to triage** vs single-source baselines; +133% adversarial perturbation recovery.
- **[vuln-attribution](https://github.com/benseverndev-oss/goldenmatch-vuln-attribution)** — cross-database ER on 6.1M OSS vulnerability records across 40 sources. **6,126,895 records → 847,475 canonical vulns** in ~5 minutes on a single 64GB runner via the full suite.
- **[sanctions-reconciliation](https://github.com/benseverndev-oss/goldenmatch-sanctions-reconciliation)** — cross-list coverage on 85 public sanctions lists across 50+ jurisdictions, plus 10-year OFAC SDN history and PEP/crypto cross-analysis. A coverage-gap benchmark for any screening vendor.

---

## Choose your path

| I want to... | Go here |
|---|---|
| Deduplicate a CSV right now | [`goldenmatch` quick start](packages/python/goldenmatch/README.md#quick-start) |
| Match records from PDFs / images (unstructured input) | [document ingest](https://docs.bensevern.dev/goldenmatch/documents) |
| Use from Claude Desktop / Code | [`goldenmatch` — MCP](packages/python/goldenmatch/README.md#remote-mcp-server) |
| Edit rules in a browser, label pairs, compare runs | [`goldenmatch` — Web UI](packages/python/goldenmatch/README.md#web-ui) |
| Build AI agents that deduplicate | [ER Agent / A2A wiki](https://github.com/benseverndev-oss/goldenmatch/wiki/ER-Agent) |
| Profile data quality before matching | [`goldencheck`](packages/python/goldencheck/README.md) |
| Standardize messy fields (phone, date, address) | [`goldenflow`](packages/python/goldenflow/README.md) |
| Run the full pipeline declaratively | [`goldenpipe`](packages/python/goldenpipe/README.md) |
| Map columns across schemas | [`infermap`](packages/python/infermap/README.md) |
| Analyze + report across stages and runs | [`goldenanalysis`](packages/python/goldenanalysis/README.md) |
| Write TypeScript / Node / Edge (optional WASM) | [`packages/typescript/goldenmatch`](packages/typescript/goldenmatch/README.md) |
| Match in Postgres / DuckDB SQL | [`packages/rust/extensions`](packages/rust/extensions/README.md) |
| Add data-quality gates to dbt | [`dbt-goldensuite`](packages/dbt/goldensuite/README.md) |
| Block bad data in GitHub PRs | [`goldencheck-action`](packages/actions/goldencheck/README.md) |
| Run as Airflow DAGs | [`examples/airflow/`](examples/airflow/README.md) — 13 drop-in DAGs |
| Run from a single MCP container | [`goldensuite-mcp`](packages/python/goldensuite-mcp/README.md) |

---

## Quick examples

**Python — dedupe in 30 seconds**

```python
import goldenmatch as gm

result = gm.dedupe("customers.csv")               # zero-config
print(result)                                     # DedupeResult(records=5000, clusters=847, match_rate=12.0%)
result.golden.write_csv("deduped.csv")

result = gm.dedupe("customers.csv",               # or be explicit
    exact=["email"], fuzzy={"name": 0.85, "zip": 0.95},
    blocking=["zip"], threshold=0.85)
```

**TypeScript — edge-safe core**

```typescript
import { dedupe } from "goldenmatch";

const result = dedupe(rows, { fuzzy: { name: 0.85 }, blocking: ["zip"], threshold: 0.85 });
console.log(result.stats);   // { totalRecords, totalClusters, matchRate, ... }
```

Runs in browsers, Vercel Edge, Cloudflare Workers, Deno — and optionally swaps in the Rust `score-core` kernel via `await enableWasm()`. ~940 tests, strict TypeScript.

**Composed pipeline**

```python
import goldenpipe as gp

pipeline = gp.Pipeline.from_yaml("pipeline.yaml")   # check → flow → match
result = pipeline.run("customers.csv")
result.report.write_html("report.html")
```

**Web workbench** — `pip install 'goldenmatch[web]'` then `goldenmatch serve-ui my-project` (opens `http://localhost:5050`): edit rules with live validation, preview against a sampled slice, label pairs (mirrored into Learning Memory), compare runs, sweep parameters.

**More**: [`examples/`](examples/README.md) has runnable demos —
[Python](examples/python/README.md) (quickstart, full pipeline, customer 360, PPRL, review, MCP client) ·
[TypeScript](examples/typescript/README.md) (quickstart, Vercel Edge, MCP client) ·
[Airflow](examples/airflow/README.md) (production-shaped DAGs).

---

## Install

**The whole suite, configured for speed** — the [`golden-suite`](packages/python/golden-suite/README.md) meta-package pulls in every package plus the native (Rust) kernels, pinned to compatible versions and defaulted to the perf-optimized config (native paths on, no env vars). The native wheels are **hard** dependencies on purpose: a platform without a wheel fails loudly rather than silently running the slow pure-Python path.

```bash
pip install golden-suite
golden-suite doctor        # verify every package + native kernel is importable and healthy
golden-suite optimize      # repair / re-enable the perf-optimized config

pip install golden-suite[mcp]     # + the aggregator MCP server (every tool, one endpoint)
pip install golden-suite[agent]   # + GoldenPipe serving surfaces (A2A + REST + TUI)
pip install golden-suite[all]     # everything
```

**Just GoldenMatch** — ships fat optional extras so you only pay for what you use (native acceleration is already default on common platforms):

```bash
pip install goldenmatch                    # core (CSV in, CSV out) + native
pip install goldenmatch[documents]         # + PDF/image ingest (run on unstructured input)
pip install goldenmatch[embeddings]        # + sentence-transformers, FAISS
pip install goldenmatch[llm]               # + Claude / OpenAI for LLM boost
pip install goldenmatch[duckdb]            # + DuckDB out-of-core backend
pip install goldenmatch[ray]               # + Ray distributed backend (50M+ rows)
pip install goldenmatch[postgres]          # + Postgres sync  (also: [snowflake] [bigquery] [databricks] [salesforce])
pip install goldenmatch[quality]           # + GoldenCheck    (also: [transform] for GoldenFlow)
pip install goldenmatch[mcp]               # + MCP server     (also: [agent] A2A, [web] browser workbench)

goldenmatch setup    # interactive wizard: GPU, API keys, database
```

Sister packages compose: `pip install goldenpipe[full]` brings in Check + Flow + Match together.

---

## Deploy

### Remote MCP (nothing to install)

Hosted on [Smithery](https://smithery.ai/servers/benzsevern/goldenmatch) — connect any MCP client:

```json
{ "mcpServers": { "goldenmatch": { "url": "https://goldenmatch-mcp-production.up.railway.app/mcp/" } } }
```

70+ MCP tools across the suite: deduplicate, match, explain, review, link privately, configure, scan quality, transform, synthesize golden records, analyze trends and regressions, manage Learning Memory.

### Containers

Every package ships as a multi-arch image (linux/amd64 + arm64) on GHCR — pull anonymously:

```bash
docker run -p 8300:8300 ghcr.io/benseverndev-oss/goldensuite-mcp:latest   # one container, every tool
docker run -p 8200:8200 ghcr.io/benseverndev-oss/goldenmatch-mcp:latest   # per-package (also: goldencheck/goldenflow/goldenpipe/infermap -mcp)
docker run -e POSTGRES_PASSWORD=secret ghcr.io/benseverndev-oss/goldenmatch-extensions:latest   # Postgres + extension
```

Tags: `:latest` (current `main`), `:main-<sha7>` (every push, immutable), `:vX.Y.Z` / `:vX.Y` (on release). See [`goldensuite-mcp`](packages/python/goldensuite-mcp/README.md) for the aggregator's tool-collision behaviour.

### Airflow

13 drop-in DAGs at [`examples/airflow/`](examples/airflow/README.md) (TaskFlow API, Airflow 2.7+ / 3.x; tunable knobs, idempotent retries, marker-protected against double-processing), grouped by lifecycle stage:

| Group | DAGs |
|---|---|
| **Core pipeline** | `daily_dedupe`, `incremental_match`, `warehouse_native` (Snowflake), `customer_360`, `identity_graph` |
| **Privacy** | `pprl_linkage` (two-party PPRL) |
| **Onboarding & monitoring** | `schema_align_and_load`, `schema_drift_alarm`, `quality_gate` |
| **Feedback loop** | `review_worker`, `active_learning` |
| **Operationalize** | `reverse_etl` (Salesforce/HubSpot), `backfill` |

---

## Benchmarks & scale

Published GoldenMatch numbers (DQbench composite **91.04**, DBLP-ACM **0.9641** F1, Febrl3 **0.9443** F1, NCVR **0.9719** F1) map back to a single committed runner, `scripts/run_benchmarks.py`. See [`docs/reproducing-benchmarks.md`](docs/reproducing-benchmarks.md) for per-number commands, dataset URLs, expected output with tolerance, and a one-click reproduction snippet. The same runner powers the weekly `benchmarks.yml` workflow.

**Scale envelope** ([`docs/scale-envelope.md`](docs/scale-envelope.md)) — per-backend ranges (Polars in-memory < 500K, DuckDB out-of-core 500K–50M, Ray distributed ≥ 50M), block-size failure modes, candidate-pair math, and a decision tree for picking a backend.

**Verified at the top end:** a full **100M-row** dedupe on a 5-node Ray cluster (`e2-standard-16`, 80 CPU) in **9.2 min** (554 s), **20,000,000 golden records recovered exactly**, driver peak **0.36 GB RSS**. The default distributed path is **recall-complete** (blocking-key shuffle scoring + distributed randomized-contraction WCC), so duplicates merge correctly *no matter how the input is partitioned*, and it stays driver-collect-free end to end (#844). A faster per-partition path (`GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE=0`, ~213 s on a 4-worker run) suits inputs where duplicates already co-locate within partitions. Recipe: [`configs/distributed-100m.yaml`](packages/python/goldenmatch/configs/distributed-100m.yaml).

---

## Repository layout

```
goldenmatch/
├── packages/
│   ├── python/        goldenmatch · goldencheck · goldenflow · goldenpipe · infermap · goldenanalysis
│   │                  goldensuite-mcp (aggregator) · golden-suite (meta)
│   ├── typescript/    full TS ports (edge-safe cores + WASM) · goldencheck-types
│   ├── rust/extensions/  Postgres pgrx + DuckDB UDFs (own Cargo workspace)
│   ├── dbt/goldensuite/  dbt materializations, tests, macros
│   └── actions/goldencheck/  GitHub Action
├── examples/          python · typescript · airflow (drop-in DAGs)
├── docs/superpowers/  design specs and implementation plans
├── justfile · pyproject.toml (uv workspace) · pnpm-workspace.yaml (Turborepo) · .github/workflows/ci.yml
```

- **Cargo — no root workspace.** `packages/rust/extensions/` is itself a Cargo workspace (the `postgres` crate is excluded for pgrx build requirements); Cargo commands run from inside it.
- **TypeScript — one pnpm workspace.** `packages/typescript/*` form a single pnpm + Turborepo workspace; `.npmrc` pins `node-linker=hoisted` for a flat `node_modules` (avoids Windows symlink issues).

```bash
just install   # uv sync + per-package npm install + cargo fetch
just test      # all languages   ·   just lint   ·   just build
```

---

## Contributing

- Feature work on `feature/<name>` branches; merge via squash PR. Titles: `feat:` / `fix:` / `docs:`.
- Tests must pass on all three languages where the change applies; the parity harness in `packages/typescript/goldenmatch/tests/parity/` enforces 4-decimal Python ↔ TypeScript scorer parity.
- See `docs/superpowers/specs/` for design rationale.

**TypeScript dev setup (pnpm + Turborepo)** — from the repo root:

```bash
corepack enable                               # one-time, picks up pnpm@9.15.0 from package.json
pnpm install
pnpm turbo run build test typecheck lint      # full pipeline (cached after first run)
```

**Windows:** enable Developer Mode (Settings → For Developers) so `pnpm install` can create symlinks; if `corepack enable` needs admin, `npm i -g pnpm@9.15.0` is equivalent.

---

<sub>This repo was formed on **2026-05-01** by folding 8 sibling repos into `goldenmatch` via `git filter-repo` (full history preserved) — [design](docs/superpowers/specs/2026-05-01-goldenmatch-monorepo-fold-in-design.md) · [plan](docs/superpowers/plans/2026-05-01-goldenmatch-monorepo-fold-in.md). Built by **[Ben Severn](https://bensevern.dev)**. MIT — see [LICENSE](LICENSE).</sub>
