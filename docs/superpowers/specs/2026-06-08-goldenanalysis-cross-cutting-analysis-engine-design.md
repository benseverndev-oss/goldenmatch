# GoldenAnalysis — Cross-Cutting Analysis Engine (design)

**Status:** Design proposed. No implementation yet — this doc is the first PR.
**Owner:** GoldenAnalysis (new package).
**Targets:** new `goldenanalysis` package on Python + TypeScript + Rust (`*-core` / `*-native` split). No version bumps to existing packages required; integration is additive and opt-in.
**Date:** 2026-06-08

---

## Problem

The suite has five product layers, each of which **emits structured run artifacts** but none of which **measures, aggregates, or reports across them**:

- GoldenCheck emits a scan report (column stats, type inference, quality findings).
- GoldenFlow emits transform stats (rows changed, rule hit-counts).
- GoldenMatch emits scored pairs, clusters, a golden frame, and (v1.15+) an identity summary with conflict counts.
- GoldenPipe emits a run manifest stitching the above into `PipeResult.artifacts`.
- InferMap emits a mapping with per-field confidence.

Today, answering questions *about* a run — "what was the match rate?", "how did cluster-size distribution shift vs last week?", "which blocking key carried the recall?", "did quality-finding counts regress after the GoldenFlow rule change?", "roll all five stage outputs into one report for the data steward" — means hand-writing a notebook against each package's bespoke output shape. There is no shared vocabulary for *metrics over artifacts*, no run-over-run aggregation, and no single report surface.

GoldenCheck is sometimes mistaken for this layer, but it is **not**: GoldenCheck profiles a *single input dataset at ingest* and emits findings. It does not consume other stages' outputs, does not aggregate across runs, and is itself a *producer* of artifacts that GoldenAnalysis would consume. The gap is a **read-only, cross-cutting analysis/metrics/reporting engine** that sits *beside* the pipeline, consumes any stage's typed artifacts, and produces unified, comparable, exportable analysis.

---

## Goal

A new package `goldenanalysis` that:

1. **Ingests typed artifacts** from any suite package via a small set of adapters (GoldenCheck scan report, GoldenFlow transform stats, GoldenMatch cluster / scored-pair / identity summary, GoldenPipe manifest, InferMap mapping) — and from a raw Polars/Arrow frame for the generic case.
2. **Runs composable analyzers** (a registry, mirroring GoldenPipe's stage entry-points) that each compute a typed `Metric` set + optional tables.
3. **Aggregates across runs** — trend lines, drift, and regression detection over a history of `AnalysisReport`s keyed by `run_id`.
4. **Emits one `AnalysisReport`** with a stable schema, exportable to JSON / Markdown / Parquet, plus a one-paragraph NL summary.
5. Ships with **parity-by-construction across Python + TS** (byte-identical metric values on shared fixtures) and an **optional Rust accelerator** (`goldenanalysis[native]`) for the heavy aggregation primitives, gated exactly like `goldenmatch[native]` / `goldencheck[native]`.

**Non-goal:** GoldenAnalysis does **not** transform data, mutate any store, or make pipeline decisions. It is read-only. It is *not* a GoldenPipe stage that gates the run (it may be invoked *after* a run as a reporting step, but its analyzers never write back into the dataset). It does not replace GoldenCheck's ingest-time profiling.

---

## Why a new package and not a GoldenCheck/GoldenPipe feature

| Option | Why rejected |
|---|---|
| Fold into **GoldenCheck** | GoldenCheck is single-dataset ingest profiling and a *producer* of artifacts GoldenAnalysis consumes. Making it also consume GoldenMatch/identity outputs inverts its dependency direction (GoldenCheck would import goldenmatch) and muddies its "validation by discovery" promise. |
| Fold into **GoldenPipe** | GoldenPipe orchestrates *forward* execution and is decision-bearing. Cross-run aggregation, drift, and reporting are read-only and run-history-shaped, not orchestration. GoldenPipe would *consume* GoldenAnalysis as an optional terminal reporting stage, not contain it. |
| A `scripts/` notebook | No shared schema, no parity surface, no reuse across the five packages, no published artifact. The whole problem is the absence of a *vocabulary*. |

A standalone layer keeps each existing package single-purpose, gives the metrics/report schema one home, and matches the suite's established "one product layer = one package, P+TS+Rust" shape.

---

## Design

### Package identity

- **Tagline:** "Measure and report across the Golden Suite."
- **Pipeline position:** sideband / terminal. `… GoldenMatch.dedupe → GoldenMatch.identity_resolve → [GoldenAnalysis.report]`.
- **Direction of dependency:** GoldenAnalysis depends (optionally) on the other packages' *types* only, never the reverse for core. It consumes their outputs; it is imported by nothing in their hot paths.

### Core domain types (Python; TS mirrors field-for-field)

```python
# goldenanalysis/models/report.py
class Metric(BaseModel):
    key: str                 # dotted, stable: "match.pair_count", "cluster.size_p95"
    value: float | int | str
    unit: str | None = None  # "rows", "ratio", "ms", None
    direction: Literal["higher_better", "lower_better", "neutral"] = "neutral"

class AnalysisTable(BaseModel):
    name: str                # "cluster_size_histogram"
    columns: list[str]
    rows: list[list[Any]]    # small, report-embeddable; large tables go to Parquet sidecar

class AnalysisReport(BaseModel):
    schema_version: int = 1
    run_id: str
    generated_at: datetime
    source: dict[str, str]            # which artifacts fed this report
    metrics: list[Metric]
    tables: list[AnalysisTable]
    narrative: str | None = None      # one-paragraph NL summary
    analyzers_run: list[str]
```

`schema_version` is the cross-surface contract anchor (same discipline as the identity store's `schema_version`); bumping it requires a parity-test update on both Python and TS.

### Artifact adapters

One adapter per producer, each normalizing a package's native output into the inputs an analyzer expects. Adapters live behind optional extras so the core install stays dependency-light:

```python
# goldenanalysis/adapters/match.py
class MatchArtifactAdapter:
    """Normalizes goldenmatch DedupeResult / identity summary into AnalyzerInput."""
    def load(self, result: "goldenmatch.DedupeResult") -> AnalyzerInput: ...

# goldenanalysis/adapters/check.py    -> goldencheck ScanReport
# goldenanalysis/adapters/flow.py     -> goldenflow transform stats
# goldenanalysis/adapters/pipe.py     -> goldenpipe PipeResult.artifacts (multi-stage)
# goldenanalysis/adapters/frame.py    -> raw polars.DataFrame (generic fallback)
```

The `frame` adapter is the always-available generic path (zero suite deps), so GoldenAnalysis is useful on any DataFrame even with no other package installed.

### Analyzer registry (mirrors `goldenpipe.stages`)

Analyzers are discovered via entry-points, so third parties and other suite packages can register their own without editing GoldenAnalysis:

```toml
# packages/python/goldenanalysis/pyproject.toml
[project.entry-points."goldenanalysis.analyzers"]
"match.rates"          = "goldenanalysis.analyzers.match_rates:MatchRatesAnalyzer"
"cluster.distribution" = "goldenanalysis.analyzers.cluster_dist:ClusterDistributionAnalyzer"
"quality.rollup"       = "goldenanalysis.analyzers.quality_rollup:QualityRollupAnalyzer"
"frame.summary"        = "goldenanalysis.analyzers.frame_summary:FrameSummaryAnalyzer"
```

```python
# goldenanalysis/analyzers/base.py
class Analyzer(Protocol):
    info: AnalyzerInfo                 # name, consumes=[...], produces metric keys
    def run(self, inp: AnalyzerInput) -> AnalyzerResult: ...   # metrics + tables
```

Ship four analyzers in the first cut (see Phasing). The registry pattern is the extensibility story; the four shipped analyzers are the proof.

### Cross-run aggregation

```python
# goldenanalysis/history.py
class ReportHistory:
    """Append-only store of AnalysisReports keyed by (analysis_name, run_id)."""
    def append(self, report: AnalysisReport) -> None: ...
    def trend(self, metric_key: str, last_n: int = 30) -> TrendSeries: ...
    def detect_regressions(self, baseline: str = "previous",
                           threshold_pct: float = 10.0) -> list[Regression]: ...
```

Backend mirrors the identity store's pluggability: default JSONL on disk, optional SQLite, same `backend=` / `path=` / `connection=` constructor shape as `IdentityStore` so the suite has *one* persistence idiom.

### Public API

```python
import goldenanalysis as ga

# Generic frame (no other package needed)
report = ga.analyze(df, analyzers=["frame.summary"])

# Over a GoldenMatch result
report = ga.analyze_match(dedupe_result)        # runs match.* + cluster.*
print(report.to_markdown())
report.to_json("report.json"); report.to_parquet("report.parquet")

# Whole pipeline manifest
report = ga.analyze_pipeline(pipe_result)

# Cross-run
hist = ga.ReportHistory(backend="sqlite", path=".golden/analysis.db")
hist.append(report)
regressions = hist.detect_regressions(threshold_pct=10.0)
```

CLI (Typer, mirrors the other packages' `[project.scripts]` convention):

```bash
goldenanalysis report match_result.json --format markdown
goldenanalysis report customers.parquet --analyzers frame.summary
goldenanalysis trend --metric match.pair_count --history .golden/analysis.db --last 30
goldenanalysis regressions --history .golden/analysis.db --threshold 10
```

---

## The three surfaces

### Python — `packages/python/goldenanalysis/`

Layout mirrors goldenpipe/goldencheck:

```
goldenanalysis/
  __init__.py            # analyze(), analyze_match(), analyze_pipeline(), ReportHistory
  _api.py
  py.typed
  models/                # Metric, AnalysisTable, AnalysisReport, AnalyzerInput/Result
  adapters/              # match, check, flow, pipe, infermap, frame
  analyzers/             # base + the four shipped analyzers
  core/
    _native_loader.py    # GOLDENANALYSIS_NATIVE gate (copy of goldencheck's)
    aggregate.py         # pure-Python/Polars reference primitives
  history.py
  cli/main.py            # [project.scripts] goldenanalysis = "goldenanalysis.cli.main:app"
  mcp/                   # optional, [analysis] extra — analyze_artifact / get_trend tools
tests/
  conftest.py
  fixtures/              # shared P/TS parity fixtures (committed JSON)
  test_analyzers.py
  test_native_parity.py  # native == pure-Python, gated like goldencheck
  test_report_schema.py
CHANGELOG.md  README.md  LICENSE  pyproject.toml  server.json  golden-suite.json
```

`pyproject.toml` follows the goldenpipe template (hatchling, Typer/rich/pydantic/polars core deps, optional extras for each adapter + `native`/`mcp`/`api`):

```toml
[project.optional-dependencies]
match = ["goldenmatch>=1.15.0"]
check = ["goldencheck>=1.2.0"]
flow  = ["goldenflow>=1.1.5"]
pipe  = ["goldenpipe>=1.2.0"]
suite = ["goldenanalysis[match,check,flow,pipe]"]
native = ["goldenanalysis-native"]   # optional Rust accelerator
mcp   = ["mcp>=1.0"]
api   = ["fastapi>=0.110", "uvicorn>=0.30"]
dev   = ["pytest>=8.0", "pytest-cov>=5.0", "ruff>=0.6"]
```

Workspace registration (per `packages/python/CLAUDE.md`, BOTH must change):

```toml
# /pyproject.toml
[tool.uv.workspace] members = ["packages/python/*"]   # already globs goldenanalysis
[tool.uv.sources]
goldenanalysis = { workspace = true }
goldenanalysis-native = { path = "packages/rust/extensions/analysis-native" }
```

### TypeScript — `packages/typescript/goldenanalysis/`

Mirrors goldenpipe TS: own `package.json` (not a pnpm-workspace member — Windows EISDIR), `src/{index.ts, cli.ts, core/, node/}`, `tests/{unit,parity,fixtures}`, tsup + vitest + tsconfig. camelCase fields per the TS CLAUDE.md, **except** the `AnalysisReport`/`Metric` wire types, which keep the Python snake_case keys (same exception as `goldencheck-types`) so reports cross the JSON wire between surfaces without remapping. Edge-safe (`import type`, `.js` suffixes).

Parity is enforced by `tests/parity/` reading the same committed `fixtures/*.json` the Python `test_report_schema.py` reads, asserting identical metric values.

### Rust — `*-core` / `*-native` split

Two crates under `packages/rust/extensions/`, modeled exactly on the `goldencheck-core` (pyo3-free, standalone workspace, path dep) + `goldencheck-native` (abi3 maturin wheel) pair:

```
packages/rust/extensions/analysis-core/      # pyo3-free aggregation primitives
  Cargo.toml   (standalone [workspace], no rust-toolchain.toml — inherits parent)
  src/lib.rs   histogram, quantiles (P²/streaming), group-by rollup, reservoir summary
packages/rust/extensions/analysis-native/    # abi3 ext-module wheel
  Cargo.toml   pyproject.toml
  python/goldenanalysis_native/__init__.py
  src/lib.rs   PyO3 bindings delegating to analysis-core
```

The native kernel accelerates *only* the heavy aggregation primitives (large `tables` over big artifact frames). The pure-Python/Polars path in `core/aggregate.py` is the **byte-identical reference**; `analysis-core` mirrors it value-for-value. Loader gate `goldenanalysis/core/_native_loader.py` is a copy of goldencheck's, with `GOLDENANALYSIS_NATIVE` env (`0`/`1`/`auto`) and a `_GATED_ON` set that starts **empty** — a primitive only joins `_GATED_ON` after `test_native_parity.py` proves identical output. (Heed the `goldenmatch-native` lessons in root CLAUDE.md: bump `pyproject.toml` *and* `Cargo.toml` in lockstep on republish; confirm a new symbol is in the *published* wheel before assuming any env benefits; verify wall-clock moved, not just that the version shipped.)

---

## Integration with the suite (additive, opt-in)

1. **GoldenPipe terminal stage (optional, separate follow-up PR).** Register `goldenanalysis.report` at `goldenpipe.stages`, adapter at `goldenpipe/adapters/analysis.py`, `consumes=["df","clusters","identity_summary"]`, `produces=["analysis_report"]`. It appends to `PipeResult.artifacts["analysis_report"]` and writes nothing back. This makes "one CLI runs Check → Flow → Match → Identity → **Analysis**" literally true. *Not in the first PR* — GoldenAnalysis must exist standalone first.
2. **goldensuite-mcp** picks up GoldenAnalysis's MCP tools transitively once `goldenanalysis.mcp.server.TOOLS` exists (same aggregator pattern that already surfaces identity tools).
3. **No changes to GoldenCheck/Flow/Match/InferMap.** Adapters read their *existing* public output types. If an adapter needs a field a producer doesn't expose, that's a separate, named PR against that producer — never a silent coupling.

---

## CI + publish wiring (the parts that bite if skipped)

Per the post-2026-05-06 path-filter rules in root CLAUDE.md, adding a package means **adding a filter entry AND wiring the `if:` gate** in `.github/workflows/ci.yml`:

- `changes` job: add `python_goldenanalysis: ['packages/python/goldenanalysis/**']`, `analysis_native: ['packages/rust/extensions/analysis-{core,native}/**']`; the Python dynamic matrix (`ci.yml:~155`) picks up the new package automatically once it has a `tests/` dir, but the **emit step** that builds the matrix array must include it.
- TS lane already globs `packages/typescript/**`; the new package is covered.
- Native parity lane: mirror the `goldencheck_native` job (build the abi3 wheel, run `test_native_parity.py` under `GOLDENANALYSIS_NATIVE=1`).
- pytest `--ignore` list + coverage floors: seed conservatively, mirroring a young package; tighten later.

Publish workflows — three new files mirroring the existing triplet naming (root-level only; pre-fold orphans under `packages/**/.github` are ignored):

- `publish-goldenanalysis.yml` — `release: published` on `goldenanalysis-v*`, PyPI via `PYPI_TOKEN`.
- `publish-goldenanalysis-js.yml` — npm on `goldenanalysis-js-v*`.
- `publish-goldenanalysis-native.yml` — maturin wheels on `goldenanalysis-native-v*`, both macOS arches on `macos-14` (Intel runners queue forever — see CLAUDE.md).
- `publish-mcp.yml` — add `goldenanalysis` to the `package` enum so its `server.json` syncs to the registry under `io.github.benseverndev-oss/goldenanalysis`.

Derive publish version from the **git tag**, not PyPI (the duplicate-version race documented in CLAUDE.md / PR #167).

---

## Acceptance criteria for the feature this spec describes

The implementation PR(s) that follow are accepted when:

1. **Package builds + tests green on all three surfaces.** `uv run pytest packages/python/goldenanalysis`, `npm test` in the TS package, and `cargo test` in both Rust crates all pass; the package enters the CI Python matrix and the TS lane.
2. **`ga.analyze(df, analyzers=["frame.summary"])` works with zero other suite packages installed** (generic frame path, core deps only).
3. **Four analyzers shipped** — `frame.summary`, `match.rates`, `cluster.distribution`, `quality.rollup` — each with a fixture-backed test asserting exact metric values.
4. **`AnalysisReport` round-trips** JSON ↔ object ↔ Markdown ↔ Parquet; `schema_version == 1`.
5. **Cross-surface parity:** `tests/parity/` (TS) and `test_report_schema.py` (Python) read the same committed fixtures and assert identical metric values and report keys.
6. **`ReportHistory` cross-run:** append two reports, `detect_regressions(threshold_pct=10)` flags a seeded 12% drop and ignores a seeded 3% drop; deterministic.
7. **Native parity:** every primitive in `_GATED_ON` produces byte-identical output to the pure-Python reference under `GOLDENANALYSIS_NATIVE=1`; with the wheel absent, all paths fall back and tests still pass.
8. **CI wired:** path-filter entry + matrix inclusion + native parity lane; doc-only changes to the package still skip code jobs.
9. **Docs:** package README with quickstart, a "GoldenCheck vs GoldenAnalysis" disambiguation section, `CHANGELOG.md` at `0.1.0`, `server.json` + `golden-suite.json`.

### Explicit non-goals (do not creep)

- No GoldenPipe stage in the first PR (separate, named follow-up).
- No web UI / workbench tab.
- No new output from any *other* package (adapters read existing fields only; gaps become their own PRs).
- No write-back of any kind — GoldenAnalysis is read-only by construction.
- No dashboards/charts service; `to_markdown()`/`to_parquet()` is the v0.1 surface, charts are deferred.
- No `_GATED_ON` entries shipped un-parity-tested.

---

## Phasing

- **Phase 0 (this doc).** Spec committed. No code.
- **Phase 1 — Python core.** Models, `frame` adapter, `frame.summary` analyzer, `AnalysisReport` + exporters, CLI, pure-Python aggregation. Workspace-registered, in CI matrix. Ship `goldenanalysis 0.1.0`.
- **Phase 2 — suite adapters + analyzers.** `match`/`check`/`flow`/`pipe` adapters; `match.rates`, `cluster.distribution`, `quality.rollup`; `ReportHistory` + regression detection.
- **Phase 3 — TS parity.** Mirror Phase 1–2 surface; parity fixtures + lane.
- **Phase 4 — Rust accelerator.** `analysis-core` + `analysis-native`; loader gate; parity lane; first `_GATED_ON` primitive (likely the cluster-size histogram on large frames — verify the wall actually moves on a real shape before gating, per the perf-audit lesson).
- **Phase 5 — GoldenPipe terminal stage + goldensuite-mcp surfacing.**

Each phase is an independent, releasable PR; Phase 1 alone is a usable product.

---

## Risks / unknowns

- **Overlap perception with GoldenCheck.** Mitigated by the read-only, consumes-other-stages, cross-run framing and a README disambiguation section. The dependency direction (GoldenAnalysis → others' types, never reverse) is the hard line that keeps them distinct.
- **Adapter coupling to producer output shapes.** Producers evolve their result objects. Mitigation: adapters depend on the *typed* public result classes (versioned via the optional extras' `>=` pins), and a contract test per adapter asserts the fields it reads still exist. A producer-side field gap is a named PR, never a silent reach into internals.
- **`scored_pairs` availability for `match.rates`.** Same coupling noted in the GoldenPipe identity spec: a `DedupeResult` may not retain `scored_pairs` by default. The adapter degrades gracefully — it computes what the available artifacts support and records *which* metrics it could not compute in `report.source`, rather than failing.
- **Native parity drift / wheel skew.** The exact `goldenmatch-native` footguns (symbol-skew slow fallback, pyproject-vs-Cargo version drift, ship-vs-wall confusion) apply verbatim. Start `_GATED_ON` empty; add primitives one parity-proven, wall-verified step at a time.
- **Parquet as a core dependency.** polars+pyarrow already in the suite's dependency graph, so `to_parquet` adds no new heavyweight dep; the generic frame path needs polars regardless.

---

## What this PR changes in the repo

Only this spec doc. No package code, no workspace edits, no CI changes. The next PR is **Phase 1** (Python core, `goldenanalysis 0.1.0`) per the acceptance criteria above.
