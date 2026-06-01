<!-- mcp-name: io.github.benseverndev-oss/goldenmatch -->
<div align="center">

# 🟡 Golden Suite

**A polyglot data-quality and entity-resolution toolkit. Polished, opinionated, AI-native.**

*GoldenCheck profiles → GoldenFlow standardizes → GoldenMatch deduplicates → GoldenPipe orchestrates. With InferMap for schema mapping and a Rust extension layer for Postgres / DuckDB.*

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
[![DQBench ER](https://img.shields.io/badge/DQBench%20ER-95.30-d4a017)](https://github.com/benseverndev-oss/dqbench)
[![DBLP-ACM F1](https://img.shields.io/badge/DBLP--ACM%20F1-97.2%25-d4a017)](packages/python/goldenmatch/README.md#benchmarks)

<!-- Reach -->
[![PyPI downloads (suite)](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fbenseverndev-oss%2Fgoldenmatch%2Fbadges%2Fpypi-downloads.json)](https://pepy.tech/projects?q=goldenmatch+goldencheck+goldenpipe+goldenflow+infermap+goldencheck-types)
[![npm downloads (suite)](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fbenseverndev-oss%2Fgoldenmatch%2Fbadges%2Fnpm-downloads.json)](https://www.npmjs.com/~benzsevern)
[![GitHub stars](https://img.shields.io/github/stars/benseverndev-oss/goldenmatch?style=flat&color=d4a017&logo=github)](https://github.com/benseverndev-oss/goldenmatch/stargazers)

<!-- Ecosystem -->
[![Docs](https://img.shields.io/badge/docs-bensevern.dev-d4a017)](https://bensevern.dev/)
[![Wiki](https://img.shields.io/badge/wiki-github-d4a017)](https://github.com/benseverndev-oss/goldenmatch/wiki)
[![Web UI](https://img.shields.io/badge/web%20ui-FastAPI%20%2B%20React-d4a017?logo=react&logoColor=white)](https://github.com/benseverndev-oss/goldenmatch/wiki/Web-UI)
[![Smithery MCP](https://img.shields.io/badge/MCP-smithery-6e40c9)](https://smithery.ai/servers/benseverndev-oss/goldenmatch)

<!-- Activity -->
[![Last commit](https://img.shields.io/github/last-commit/benseverndev-oss/goldenmatch?color=d4a017&label=last%20commit)](https://github.com/benseverndev-oss/goldenmatch/commits/main)

</div>

[![GoldenMatch web workbench — pair drilldown with NL prose](packages/python/goldenmatch/docs/screenshots/web/web-inspector.png)](https://github.com/benseverndev-oss/goldenmatch/wiki/Web-UI)

<p align="center"><sub><em>Pair drilldown in the web workbench: cluster members, field-level diff, and a one-line NL explanation per pair. <code>pip install goldenmatch[web]</code> then <code>goldenmatch serve-ui &lt;project&gt;</code>. <a href="https://github.com/benseverndev-oss/goldenmatch/wiki/Web-UI">More screenshots →</a></em></sub></p>

```bash
# Headline package: dedupe a CSV in 30 seconds
pip install goldenmatch && goldenmatch dedupe customers.csv

# TypeScript / Edge runtimes
npm install goldenmatch
```

<!-- README-callouts:start  (auto-synced from packages/python/goldenmatch/CHANGELOG.md by scripts/sync_readme_callouts.py — edit the CHANGELOG, not this block) -->
> **🆕 v1.25.0 — Arrow-native groundwork + leaner large-N runs** — columnar pair-stream / two-frame-cluster entry points and optional Rust/Arrow-C kernels (`build_clusters`, `dedup_pairs`, `record_fingerprints`, MST oversized-split) land behind the `goldenmatch._native` extension, purely additive with the pure-Python + Polars pipeline unchanged as the default and byte-for-byte reference. Plus single-node memory wins (golden -2.6 GB, bucket -3.8 GB peak at 10M; standardize ~25-30s off the prep wall) and fixes for a silently-dropped GoldenCheck quality scan and a prep-cache `id()`-recycle flake. PRs #588-#650.
>
> **v1.16.0 — 5M records in 9.94 min, 6.4 GB peak RSS, on one 16-core node** — the new `backend="bucket"` path is now the recommended 5M-on-one-node config. 5x wall reduction and 2x peak RSS reduction vs the v1.15 chunked baseline (~50 min, 11.9 GB), with rock-solid reliability on Linux runners where the chunked path was hanging at 63 GB plateau on the same fixture. PRs #310-#326.
>
> **v1.15.0 — 5M records in ~50 min on commodity hardware** — Chunked mode now actually delivers on its "1M to 100M+" promise. The streaming `scan_csv().slice()` reader + Polars-native cross-chunk join (B) + block-keyed bucketed index (C) + DuckDB pair-store backend (D) replace a broken eager-read + Python-double-loop path that OOM-killed at 3h+ on the pre-fix 5M dispatch. **Measured: 5M records, 50 min wall, 11.9 GB peak RSS, 618,817 multi-member clusters, no OOM** on a 4c/16GB GitHub runner. Pass `backend="chunked"` with an explicit blocking config. PRs #233/#234/#235.
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
| **GoldenPipe** | orchestrator — declarative YAML pipeline wiring the four steps |

- **Zero-config defaults that admit when they're unsure** — every step has a self-verifying preflight + postflight; results carry an inspectable report instead of failing silently.
- **97.2% F1 on DBLP-ACM out of the box** for entity resolution. [DQBench ER score: 95.30](https://github.com/benseverndev-oss/dqbench).
- **Learning Memory** — corrections persist across runs and re-anchor across row reorders, so the system stops needing the same correction twice (GoldenMatch v1.6.0; off by default).
- **Privacy-preserving record linkage** — match across organizations without sharing raw data (PPRL, 92.4% F1 on FEBRL4).
- **AI-native by design** — every package ships an MCP server, a REST API, and an A2A agent surface. 36+ MCP tools across the suite, including `auto_configure` + `controller_telemetry` for v1.7-v1.12 introspection.
- **AutoConfigController visible everywhere** (v1.7-v1.12 surface-parity arc) — web `ControllerPanel`, TUI `Ctrl+A`, CLI `goldenmatch autoconfig`, REST `/autoconfig` + `/controller/telemetry`, Postgres `goldenmatch_autoconfig` + `gm_telemetry`, DuckDB UDFs, MCP/A2A telemetry tools. One JSON shape across every interface.
- **Polyglot parity** — the full suite ships on **npm** (goldenmatch, goldencheck, goldenflow, infermap, goldenpipe) alongside PyPI; the TypeScript and Python implementations track the same outputs to 4-decimal precision via a cross-language parity harness.
- **SQL-native, both engines at parity** — the same functions run inside **PostgreSQL** (pgrx extension) and **DuckDB**: dedupe / match / score / auto-config + telemetry / identity graph, plus data profiling, `evaluate`, Fellegi-Sunter probabilistic scoring, and GoldenFlow transforms.
- **Production paths** — Postgres sync, daemon mode, lineage tracking, review queues, dbt integration, GitHub Actions, and a Rust extension layer for Postgres / DuckDB.

---

## The Suite

| Package | Lang | What it does | Install |
|---|---|---|---|
| **[GoldenMatch](packages/python/goldenmatch/README.md)** 🟡 | Python · TS | Zero-config entity resolution. Fuzzy + exact + probabilistic + LLM. Headline package. | `pip install goldenmatch` · `npm i goldenmatch` |
| **[GoldenCheck](packages/python/goldencheck/README.md)** | Python · TS | Data-quality scanning: encoding, Unicode, format validation, anomaly detection. | `pip install goldencheck` · `npm i goldencheck` |
| **[GoldenFlow](packages/python/goldenflow/README.md)** | Python · TS | Transforms & standardizers: phone, date, address, categorical normalization. | `pip install goldenflow` · `npm i goldenflow` |
| **[GoldenPipe](packages/python/goldenpipe/README.md)** | Python · TS | Orchestrator that wires Check → Flow → Match into one declarative pipeline. | `pip install goldenpipe` · `npm i goldenpipe` |
| **[InferMap](packages/python/infermap/README.md)** | Python · TS | Schema mapping engine — auto-aligns columns across heterogeneous sources. | `pip install infermap` · `npm i infermap` |
| **[goldenmatch-extensions](packages/rust/extensions/README.md)** | Rust | Postgres extension (pgrx) + DuckDB UDFs. SQL-native fuzzy matching. | source build |
| **[dbt-goldensuite](packages/python/goldenmatch/dbt-goldensuite/README.md)** | dbt · Python | dbt package — quality-gate tests, correction CRUD macros + GoldenCheck assertions for warehouse models. | `pip install dbt-goldensuite` |
| **[goldencheck-action](packages/actions/goldencheck/README.md)** | YAML | GitHub Action — fail PRs that introduce data-quality regressions. | Marketplace |

> Headline pitch and the deepest docs live in **[packages/python/goldenmatch/README.md](packages/python/goldenmatch/README.md)** (910 lines, full feature list, CLI, architecture, benchmarks).

---

## Choose your path

| I want to... | Go here |
|---|---|
| Deduplicate a CSV right now | [`packages/python/goldenmatch`](packages/python/goldenmatch/README.md#quick-start) |
| Use from Claude Desktop / Code | [`packages/python/goldenmatch` — MCP](packages/python/goldenmatch/README.md#remote-mcp-server) |
| Edit rules in a browser, label pairs, compare runs | [`packages/python/goldenmatch` — Web UI](packages/python/goldenmatch/README.md#web-ui) |
| Build AI agents that deduplicate | [ER Agent / A2A wiki page](https://github.com/benseverndev-oss/goldenmatch/wiki/ER-Agent) |
| Profile data quality before matching | [`packages/python/goldencheck`](packages/python/goldencheck/README.md) |
| Standardize messy fields (phone, date, address) | [`packages/python/goldenflow`](packages/python/goldenflow/README.md) |
| Run the full pipeline declaratively | [`packages/python/goldenpipe`](packages/python/goldenpipe/README.md) |
| Map columns across schemas | [`packages/python/infermap`](packages/python/infermap/README.md) |
| Write TypeScript / Node.js / Edge | [`packages/typescript/goldenmatch`](packages/typescript/goldenmatch/README.md) |
| Match in Postgres / DuckDB SQL | [`packages/rust/extensions`](packages/rust/extensions/README.md) |
| Add data-quality gates to dbt | [`packages/python/goldenmatch/dbt-goldensuite`](packages/python/goldenmatch/dbt-goldensuite/README.md) |
| Block bad data in GitHub PRs | [`packages/actions/goldencheck`](packages/actions/goldencheck/README.md) |
| Run as Airflow DAGs | [`examples/airflow/`](examples/airflow/README.md) — 12 drop-in DAGs |
| Run from a single MCP container | [`docker run ghcr.io/benseverndev-oss/goldensuite-mcp:latest`](packages/python/goldensuite-mcp/README.md) |
| Pull every Suite container | [GitHub Packages](https://github.com/benzsevern?tab=packages) |

---

## Quick examples

### Python — dedupe in 30 seconds

```python
import goldenmatch as gm

# Zero-config
result = gm.dedupe("customers.csv")
print(result)  # DedupeResult(records=5000, clusters=847, match_rate=12.0%)
result.golden.write_csv("deduped.csv")

# Or be explicit
result = gm.dedupe("customers.csv",
    exact=["email"],
    fuzzy={"name": 0.85, "zip": 0.95},
    blocking=["zip"],
    threshold=0.85)
```

### TypeScript — edge-safe core

```typescript
import { dedupe } from "goldenmatch";

const result = dedupe(rows, {
  fuzzy: { name: 0.85 },
  blocking: ["zip"],
  threshold: 0.85,
});
console.log(result.stats);  // { totalRecords, totalClusters, matchRate, ... }
```

Runs in browsers, Vercel Edge, Cloudflare Workers, Deno. 478 tests, strict TypeScript (`noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`).

### Web workbench — browser UI for matching

```bash
pip install 'goldenmatch[web]'
goldenmatch serve-ui my-project   # opens http://localhost:5050
```

![GoldenMatch web UI](packages/python/goldenmatch/docs/screenshots/web/web-inspector.png)

Edit rules with live validation, preview against a sampled slice, label pairs
(mirrored into Learning Memory automatically), compare runs (CCMS), sweep
parameters, browse the corrections store. Single-process localhost workbench
shipped as the optional `[web]` extra.

### Composed pipeline

```python
import goldenpipe as gp

pipeline = gp.Pipeline.from_yaml("pipeline.yaml")  # check → flow → match
result = pipeline.run("customers.csv")
result.report.write_html("report.html")
```

**More**: [`examples/`](examples/README.md) has runnable demos for every Suite scenario:
[Python](examples/python/README.md) (quickstart, full pipeline, customer 360, PPRL, review workflow, MCP client) ·
[TypeScript](examples/typescript/README.md) (quickstart, Vercel Edge route, MCP client) ·
[Airflow DAGs](examples/airflow/README.md) (12 production-shaped pipelines).

---

## Use cases (real-world pipelines)

Reproducible end-to-end pipelines running GoldenMatch on public data at scale, each with measured headline numbers vs baselines:

- 🕵️ **[goldenmatch-shell-company-network](https://github.com/benseverndev-oss/goldenmatch-shell-company-network)** — investigative ER across ICIJ Offshore Leaks + OpenSanctions + GLEIF + UK PSC + UK disqualified-directors. Confidence-weighted graph, structure mining, named investigative candidates. **−62.5% analyst-hours to triage** vs single-source baselines; +133% adversarial perturbation recovery.
- 🛡️ **[goldenmatch-vuln-attribution](https://github.com/benseverndev-oss/goldenmatch-vuln-attribution)** — cross-database ER on 6.1M OSS vulnerability records across 40 sources (OSV, GHSA, PyPA, RustSec, Go vulndb, EPSS, CISA KEV, CVE Project bulk). **6,126,895 records → 847,475 canonical vulns** in ~5 minutes end-to-end on a single 64GB runner via the full Golden Suite (Check + Flow + Match + Pipe).
- ⚖️ **[goldenmatch-sanctions-reconciliation](https://github.com/benseverndev-oss/goldenmatch-sanctions-reconciliation)** — cross-list coverage analysis on 85 public sanctions lists across 50+ jurisdictions via OpenSanctions, plus 10-year OFAC SDN history and PEP/crypto cross-analysis. Coverage-gap benchmark for any sanctions-screening vendor.

---

## Install variants

GoldenMatch ships fat optional extras so you only pay for what you use:

```bash
pip install goldenmatch                    # core (CSV in, CSV out) + native acceleration on common platforms
pip install goldenmatch[native]            # back-compat alias; native is already default on common platforms
pip install goldenmatch[embeddings]        # + sentence-transformers, FAISS
pip install goldenmatch[llm]               # + Claude / OpenAI for LLM boost
pip install goldenmatch[postgres]          # + Postgres sync
pip install goldenmatch[snowflake]         # + Snowflake connector
pip install goldenmatch[bigquery]          # + BigQuery connector
pip install goldenmatch[databricks]        # + Databricks connector
pip install goldenmatch[salesforce]        # + Salesforce connector
pip install goldenmatch[duckdb]            # + DuckDB out-of-core backend
pip install goldenmatch[ray]               # + Ray distributed backend (50M+ rows)
pip install goldenmatch[quality]           # + GoldenCheck integration
pip install goldenmatch[transform]         # + GoldenFlow integration
pip install goldenmatch[mcp]               # + MCP server for Claude Desktop
pip install goldenmatch[agent]             # + A2A agent (aiohttp)
pip install goldenmatch[web]               # + localhost browser workbench (FastAPI + React)

goldenmatch setup    # interactive wizard: GPU, API keys, database
```

Sister packages compose: `pip install goldenpipe[full]` brings in Check + Flow + Match together.

---

## Remote MCP Server

GoldenMatch is hosted as an MCP server on [Smithery](https://smithery.ai/servers/benseverndev-oss/goldenmatch) — connect from any MCP client without installing anything.

```json
{
  "mcpServers": {
    "goldenmatch": {
      "url": "https://goldenmatch-mcp-production.up.railway.app/mcp/"
    }
  }
}
```

36+ MCP tools across the suite: deduplicate, match, explain, review, link privately, configure, scan quality, transform, synthesize golden records, and manage Learning Memory corrections.

---

## Container images

Every Suite package ships as a multi-arch container image (linux/amd64 + linux/arm64) on GitHub Container Registry. Pull anonymously, no auth needed:

```bash
# One container, every Suite tool — the convenience option
docker run -p 8300:8300 ghcr.io/benseverndev-oss/goldensuite-mcp:latest

# Per-package containers — narrower deployments
docker run -p 8200:8200 ghcr.io/benseverndev-oss/goldenmatch-mcp:latest
docker run -p 8100:8100 ghcr.io/benseverndev-oss/goldencheck-mcp:latest
docker run -p 8150:8150 ghcr.io/benseverndev-oss/goldenflow-mcp:latest
docker run -p 8250:8250 ghcr.io/benseverndev-oss/goldenpipe-mcp:latest
docker run -p 8400:8400 ghcr.io/benseverndev-oss/infermap-mcp:latest

# Postgres + extension preinstalled
docker run -e POSTGRES_PASSWORD=secret ghcr.io/benseverndev-oss/goldenmatch-extensions:latest
```

Tags:
- `:latest` — current `main`
- `:main-<sha7>` — every push to main, immutable
- `:vX.Y.Z` and `:vX.Y` — pushed when a `<package>-vX.Y.Z` tag is created

See [`packages/python/goldensuite-mcp/README.md`](packages/python/goldensuite-mcp/README.md) for the aggregator's tool-collision behaviour.

---

## Airflow

12 drop-in DAGs at [`examples/airflow/`](examples/airflow/README.md), grouped by lifecycle stage:

| Group | DAGs |
|---|---|
| **Core pipeline** | `daily_dedupe`, `incremental_match`, `warehouse_native` (Snowflake), `customer_360` (multi-source) |
| **Privacy** | `pprl_linkage` (two-party PPRL) |
| **Onboarding & monitoring** | `schema_align_and_load`, `schema_drift_alarm`, `quality_gate` |
| **Feedback loop** | `review_worker`, `active_learning` |
| **Operationalize** | `reverse_etl` (Salesforce/HubSpot), `backfill` |

TaskFlow API, Airflow 2.7+ (compatible with 3.x). Each DAG has tunable knobs at the top, idempotent retries, and is marker-protected against double-processing. Drop the file you want into your Airflow `dags/` folder.

---

## Repository layout

```
goldenmatch/
├── packages/
│   ├── python/
│   │   ├── goldenmatch/      # entity resolution — headline package
│   │   ├── goldencheck/      # data quality scanning
│   │   ├── goldenflow/       # transforms & standardizers
│   │   ├── goldenpipe/       # orchestrator
│   │   └── infermap/         # schema mapping
│   ├── typescript/
│   │   ├── goldenmatch/      # full TS port (edge-safe core)
│   │   ├── goldencheck/      # TS implementation
│   │   ├── goldencheck-types/ # shared TS types
│   │   ├── goldenflow/       # TS transforms
│   │   └── infermap/         # TS schema mapping
│   ├── rust/
│   │   └── extensions/       # Postgres pgrx + DuckDB UDFs (own Cargo workspace)
│   ├── python/goldensuite-mcp/ # aggregator MCP server (one container, all tools)
│   ├── dbt/goldencheck/      # dbt package
│   └── actions/goldencheck/  # GitHub Action
├── examples/
│   ├── python/               # 6 runnable Python scripts (quickstart → MCP)
│   ├── typescript/           # 3 TS scripts (quickstart, Vercel Edge, MCP)
│   └── airflow/              # 12 drop-in Airflow DAGs
├── docs/superpowers/         # design specs and implementation plans
├── justfile                  # install / test / lint / build, all languages
├── pyproject.toml            # uv workspace (root)
├── pnpm-workspace.yaml       # TypeScript pnpm workspace (Turborepo)
├── package.json              # root scripts + pnpm workspace root
└── .github/workflows/ci.yml
```

### Workspaces (Cargo vs pnpm)

- **Cargo — no root workspace.** `packages/rust/extensions/` is itself a Cargo workspace (the `postgres` crate is excluded for pgrx-specific build requirements). Cargo doesn't allow nested workspaces sharing members, so Cargo commands run from inside `packages/rust/extensions/`.
- **TypeScript — a single pnpm workspace.** `packages/typescript/*` form one pnpm + Turborepo workspace (see [TypeScript dev setup](#typescript-dev-setup-pnpm--turborepo)). `.npmrc` pins `node-linker=hoisted`, giving a flat `node_modules` that avoids the Windows symlink issues an earlier per-package layout hit.

### Build / test / lint everything

```bash
just install   # uv sync + per-package npm install + cargo fetch
just test      # all languages
just lint
just build
```

---

## Reproducing benchmarks

Published GoldenMatch numbers (DQbench composite 91.04, DBLP-ACM 0.9641 F1, Febrl3 0.9443 F1, NCVR 0.9719 F1) map back to a single committed runner: `scripts/run_benchmarks.py`. See [`docs/reproducing-benchmarks.md`](docs/reproducing-benchmarks.md) for per-number commands, dataset URLs, expected output (with tolerance), variance notes (deterministic vs LLM-augmented), and a copy-pasteable one-click reproduction snippet for the DQbench composite. The same runner powers the weekly `benchmarks.yml` workflow.

## Scale envelope

"How big can this handle?" is answered in [`docs/scale-envelope.md`](docs/scale-envelope.md): per-backend ranges (Polars in-memory < 500K, DuckDB out-of-core 500K - 50M, Ray distributed >= 50M), block-size failure modes, candidate-pair math, and a single-page decision tree for picking a backend.

---

## Contributing

- Feature work goes on `feature/<name>` branches; merge via squash PR.
- PR title format: `feat: <description>`, `fix: <description>`, `docs: <description>`.
- Tests must pass on all three languages where the change applies; the parity harness in `packages/typescript/goldenmatch/tests/parity/` enforces 4-decimal-tolerance Python ↔ TypeScript scorer parity.
- See `docs/superpowers/specs/` for design rationale on architectural decisions.

### TypeScript dev setup (pnpm + Turborepo)

The TypeScript packages live in a single pnpm workspace orchestrated by Turborepo. From the repo root:

```bash
corepack enable                               # one-time, picks up pnpm@9.15.0 from package.json
pnpm install                                  # installs all workspace packages
pnpm turbo run build test typecheck lint      # full pipeline (cached after first run)
pnpm --filter goldenmatch test                # single package
```

**Windows: enable Developer Mode for pnpm.** `pnpm install` creates symlinks under `node_modules/`. Settings → For Developers → Developer Mode → On. If you see `EPERM: operation not permitted, symlink ...` during install, Dev Mode is off.

If `corepack enable` fails (often needs an admin shell on Windows), the fallback is `npm i -g pnpm@9.15.0` — functionally equivalent.

---

## History

This repository was formed on **2026-05-01** by folding 8 sibling repos into the existing `goldenmatch` repo using `git filter-repo`. Full commit history is preserved for every source. See [`docs/superpowers/specs/2026-05-01-goldenmatch-monorepo-fold-in-design.md`](docs/superpowers/specs/2026-05-01-goldenmatch-monorepo-fold-in-design.md) for the design rationale and [`docs/superpowers/plans/2026-05-01-goldenmatch-monorepo-fold-in.md`](docs/superpowers/plans/2026-05-01-goldenmatch-monorepo-fold-in.md) for the step-by-step migration plan.

---

## Author & License

Built by **[Ben Severn](https://bensevern.dev)**.

MIT — see [LICENSE](LICENSE).
