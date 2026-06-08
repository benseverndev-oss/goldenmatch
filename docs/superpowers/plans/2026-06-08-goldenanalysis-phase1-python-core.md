# GoldenAnalysis Phase 1 — Python Core Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking. Each task is TDD-shaped: write the failing test, run it red, write the minimal implementation, run it green, commit.

**Goal:** Ship `goldenanalysis 0.1.0` — the Python core of the cross-cutting analysis engine. The generic frame path (`ga.analyze(df, analyzers=["frame.summary"])`) works end to end with zero other suite packages installed, produces an `AnalysisReport` that round-trips JSON/Markdown/Parquet, and is reachable from a `goldenanalysis` CLI. The package is uv-workspace-registered and runs real pytest in the CI Python matrix.

**Architecture:** Pure-Python/Polars throughout (no Rust this phase — the native loader is a stub that always falls back). One analyzer (`frame.summary`), one adapter (`frame`), the model layer, a registry keyed on the `goldenanalysis.analyzers` entry-point, report exporters, and a Typer CLI. Layout mirrors `packages/python/goldenpipe`.

**Tech Stack:** Python ≥3.11, polars, pydantic v2, typer, rich, pyarrow (for Parquet), hatchling, uv workspace, pytest.

**Spec:** `docs/superpowers/specs/2026-06-08-goldenanalysis-cross-cutting-analysis-engine-design.md` (Appendix A = metric catalog, Appendix B = the exact `frame.summary` report shape / parity fixture).

**Scope boundary (do NOT build this phase):** suite adapters (`match`/`check`/`flow`/`pipe`), the other three analyzers, `ReportHistory`/regression/`RegressionPolicy`, TS port, Rust crates, MCP/API surfaces, GoldenPipe stage. Those are Phases 2–5.

---

## Pre-flight requirements

- `uv` installed; repo synced once (`uv sync` at repo root succeeds on `main`).
- Working tree on the development branch `claude/goldencheck-rust-expansion-NmjWl`.
- `cargo`/`node` NOT required this phase.

## Conventions

- All commands run from the **repo root** (`/home/user/goldenmatch`) unless stated. Note the CLAUDE.md gotcha: CI CWD = repo root, local CWD may = package dir. **Anchor every fixture path to `__file__`**, never a bare relative path.
- "Run red" = the new test fails for the expected reason (assertion/ImportError), not a collection error elsewhere.
- "Run green" = `uv run pytest packages/python/goldenanalysis -x -q` passes.
- Tests must be self-contained for `pytest -n auto` worker isolation: register analyzers/transforms **inside** the test that asserts on them; never rely on import-time global registration leaking across workers.
- Commit at the end of every task with a `feat(goldenanalysis):` / `test(goldenanalysis):` message. Do not push until Task 1.11.

---

## Phase 1.0 — Skeleton & workspace wiring

### Task 1.0.1: Create the package skeleton

**Files:**
- `packages/python/goldenanalysis/pyproject.toml`
- `packages/python/goldenanalysis/goldenanalysis/__init__.py`
- `packages/python/goldenanalysis/goldenanalysis/py.typed`
- `packages/python/goldenanalysis/README.md` (stub; filled in Task 1.10)
- `packages/python/goldenanalysis/LICENSE` (copy `packages/python/goldenpipe/LICENSE`)

- [ ] **Step 1:** Author `pyproject.toml` from the goldenpipe template (hatchling backend). Core deps: `polars>=1.0`, `pydantic>=2.7`, `typer>=0.12`, `rich>=13.0`, `pyarrow>=15`. Optional extras exactly as the spec lists (`match`/`check`/`flow`/`pipe`/`suite`/`native`/`mcp`/`api`/`dev`). `version = "0.1.0"`. `[project.scripts] goldenanalysis = "goldenanalysis.cli.main:app"`. Add the `[project.entry-points."goldenanalysis.analyzers"]` table with the four keys (only `frame.summary` resolves this phase; the other three point at modules created in Phase 2 — **register only `frame.summary` now** to keep imports clean).
- [ ] **Step 2:** `__init__.py` exports the public names that exist this phase: `analyze`, `AnalysisReport`, `Metric` (re-exported lazily; the rest land later). `py.typed` is empty.

```bash
mkdir -p packages/python/goldenanalysis/goldenanalysis
cp packages/python/goldenpipe/LICENSE packages/python/goldenanalysis/LICENSE
```

- [ ] **Step 3: Commit.** `feat(goldenanalysis): package skeleton (pyproject, init, license)`

### Task 1.0.2: Register in the uv workspace and verify import

**Files:** `/pyproject.toml` (root)

- [ ] **Step 1:** Add to root `[tool.uv.sources]`: `goldenanalysis = { workspace = true }`. (The `[tool.uv.workspace] members = ["packages/python/*"]` glob already covers it — do not edit that line.) Per `packages/python/CLAUDE.md`, the workspace member is picked up by the glob; the `sources` entry is what lets siblings resolve it.
- [ ] **Step 2:** Sync and confirm editable import.

```bash
uv sync
uv run python -c "import goldenanalysis; print(goldenanalysis.__file__)"
# If import fails: uv pip install -e packages/python/goldenanalysis
```

Expected: prints the path under `packages/python/goldenanalysis/`.

- [ ] **Step 3: Commit.** `chore(goldenanalysis): register uv workspace source`

### Task 1.0.3: Test harness collects

**Files:**
- `packages/python/goldenanalysis/tests/conftest.py`
- `packages/python/goldenanalysis/tests/test_smoke.py`

- [ ] **Step 1 (red):** `test_smoke.py` asserts `import goldenanalysis` and `goldenanalysis.__version__ == "0.1.0"`. Run red (no `__version__` yet).
- [ ] **Step 2 (green):** add `__version__ = "0.1.0"` to `__init__.py`.

```bash
uv run pytest packages/python/goldenanalysis -q
```

- [ ] **Step 3: Commit.** `test(goldenanalysis): smoke test + version`

---

## Phase 1.1 — Model layer

### Task 1.1.1: `Metric`, `AnalysisTable`, `AnalysisReport`, analyzer I/O

**Files:**
- `packages/python/goldenanalysis/goldenanalysis/models/__init__.py`
- `packages/python/goldenanalysis/goldenanalysis/models/report.py`
- `packages/python/goldenanalysis/goldenanalysis/models/analyzer.py` (`AnalyzerInfo`, `AnalyzerInput`, `AnalyzerResult`)
- `packages/python/goldenanalysis/tests/test_models.py`

- [ ] **Step 1 (red):** `test_models.py` asserts: `Metric(key="frame.row_count", value=10, unit="rows")` defaults `direction="neutral"`; `AnalysisReport(run_id=..., generated_at=..., source={}, metrics=[], tables=[])` defaults `schema_version == 1` and `analyzers_run == []`; the report serializes via `.model_dump_json()` and re-parses equal. Run red (ImportError).
- [ ] **Step 2 (green):** implement the pydantic models exactly per the spec's "Core domain types" block. `direction` is a `Literal["higher_better","lower_better","neutral"]`. `AnalyzerResult` carries `metrics: list[Metric]` + `tables: list[AnalysisTable]`. `AnalyzerInfo` carries `name`, `consumes: list[str]`, `produces: list[str]`.
- [ ] **Step 3: Commit.** `feat(goldenanalysis): model layer (Metric, AnalysisReport, analyzer I/O)`

---

## Phase 1.2 — Pure-Python aggregation primitives

### Task 1.2.1: `core/aggregate.py`

**Files:**
- `packages/python/goldenanalysis/goldenanalysis/core/__init__.py`
- `packages/python/goldenanalysis/goldenanalysis/core/aggregate.py`
- `packages/python/goldenanalysis/tests/test_aggregate.py`

Primitives needed by `frame.summary` (and the reference path for the future Rust kernel): `null_ratio_per_column(df) -> dict[str,float]`, `duplicate_row_ratio(df) -> float`, `histogram(values, bins) -> list[tuple]`, `quantile(values, q) -> float`.

- [ ] **Step 1 (red):** `test_aggregate.py` asserts exact values on a tiny hand-built `pl.DataFrame` (e.g. 5 rows, one column 40% null → `null_ratio == 0.4`; two identical rows of five → `duplicate_row_ratio == 0.4`). Run red.
- [ ] **Step 2 (green):** implement with polars expressions only. Determinism: stable bin edges, no float-key dict ordering reliance.
- [ ] **Step 3: Commit.** `feat(goldenanalysis): pure-Python aggregation primitives`

---

## Phase 1.3 — Analyzer base + registry

### Task 1.3.1: Protocol + entry-point discovery

**Files:**
- `packages/python/goldenanalysis/goldenanalysis/analyzers/__init__.py`
- `packages/python/goldenanalysis/goldenanalysis/analyzers/base.py`
- `packages/python/goldenanalysis/goldenanalysis/registry.py`
- `packages/python/goldenanalysis/tests/test_registry.py`

- [ ] **Step 1 (red):** `test_registry.py` — `load_analyzer("frame.summary")` returns an object whose `.info.name == "frame.summary"`; `available_analyzers()` contains `"frame.summary"`. (frame_summary module lands in 1.4 — this test goes red now, green after 1.4. Mark it `xfail(reason="frame.summary lands in 1.4")` if you want a clean bar between tasks, then drop the xfail in 1.4.)
- [ ] **Step 2 (green-ish):** implement `registry.py` reading `importlib.metadata.entry_points(group="goldenanalysis.analyzers")`, with a fallback hard-coded map for editable-install reliability (entry-points sometimes miss editable members — same friction noted in `packages/python/CLAUDE.md`). `Analyzer` Protocol in `base.py`: `info: AnalyzerInfo`, `run(self, inp: AnalyzerInput) -> AnalyzerResult`.
- [ ] **Step 3: Commit.** `feat(goldenanalysis): analyzer protocol + entry-point registry`

---

## Phase 1.4 — `frame` adapter + `frame.summary` analyzer

### Task 1.4.1: The `frame` adapter

**Files:**
- `packages/python/goldenanalysis/goldenanalysis/adapters/__init__.py`
- `packages/python/goldenanalysis/goldenanalysis/adapters/frame.py`
- `packages/python/goldenanalysis/tests/test_adapter_frame.py`

- [ ] **Step 1 (red):** asserts `FrameArtifactAdapter().load(df)` returns an `AnalyzerInput` whose payload exposes the frame and a `dataset` field (defaulting to `"frame"` when unnamed). Run red.
- [ ] **Step 2 (green):** implement. The adapter is the zero-suite-dep path; it must import nothing from other suite packages.
- [ ] **Step 3: Commit.** `feat(goldenanalysis): frame adapter (generic zero-dep path)`

### Task 1.4.2: The `frame.summary` analyzer

**Files:**
- `packages/python/goldenanalysis/goldenanalysis/analyzers/frame_summary.py`
- `packages/python/goldenanalysis/tests/fixtures/__init__.py`
- `packages/python/goldenanalysis/tests/fixtures/customers_small.parquet` (committed; ~20 rows, with nulls + duplicates so every metric is exercised)
- `packages/python/goldenanalysis/tests/test_frame_summary.py`

- [ ] **Step 1 (red):** load the fixture parquet (path anchored to `__file__`), run the analyzer, assert the **exact** five metric values + the `per_column` table from Appendix A: `frame.row_count`, `frame.column_count`, `frame.null_ratio_mean` (L), `frame.duplicate_row_ratio` (L), `frame.memory_bytes`. Assert `direction` is set correctly on each. Run red.
- [ ] **Step 2 (green):** implement `FrameSummaryAnalyzer` delegating to `core/aggregate.py`. Generate the fixture with a small committed script or inline `pl.DataFrame(...).write_parquet(...)` (commit the `.parquet`, not the generator, but keep the generator in the test module as a `@pytest.fixture(scope="session")` guard that regenerates if missing — deterministic seed).
- [ ] **Step 3:** drop the `xfail` from `test_registry.py` (now green).
- [ ] **Step 4: Commit.** `feat(goldenanalysis): frame.summary analyzer + fixture`

---

## Phase 1.5 — `analyze()` + report assembly

### Task 1.5.1: Top-level `analyze()`

**Files:**
- `packages/python/goldenanalysis/goldenanalysis/_api.py`
- `packages/python/goldenanalysis/goldenanalysis/__init__.py` (wire export)
- `packages/python/goldenanalysis/tests/test_analyze.py`

- [ ] **Step 1 (red):** `ga.analyze(df, analyzers=["frame.summary"])` returns an `AnalysisReport` whose `analyzers_run == ["frame.summary"]`, `source["dataset"] == "frame"`, `schema_version == 1`, and metrics include `frame.row_count`. `ga.analyze(df)` (no analyzers arg) defaults to all *frame-compatible* analyzers (just `frame.summary` this phase). Run red.
- [ ] **Step 2 (green):** `_api.analyze()` resolves analyzers via the registry, runs each over the `frame` adapter's `AnalyzerInput`, concatenates `metrics`/`tables`, stamps `run_id` (deterministic: caller-supplied or `f"{generated_at}#{dataset}"`), and records any requested-but-unavailable analyzers in `source["unavailable"]`. No narrative yet (narrative is Phase 2, tied to regressions) — leave `narrative=None`.
- [ ] **Step 3: Commit.** `feat(goldenanalysis): analyze() entrypoint + report assembly`

---

## Phase 1.6 — Exporters

### Task 1.6.1: `to_json` / `to_markdown` / `to_parquet`

**Files:**
- `packages/python/goldenanalysis/goldenanalysis/models/report.py` (add methods)
- `packages/python/goldenanalysis/goldenanalysis/render.py` (markdown templating)
- `packages/python/goldenanalysis/tests/test_exporters.py`

- [ ] **Step 1 (red):** round-trip — `AnalysisReport.from_json(report.to_json())` equals the original; `to_markdown()` output contains the metric table header and each metric key; `to_parquet(path)` then `pl.read_parquet(path)` yields a frame with one row per metric (`key,value,unit,direction` columns) + a sidecar for tables. Run red.
- [ ] **Step 2 (green):** implement. `to_json(path=None)` returns str or writes; `to_markdown()` renders the metric table + (when present) embedded `AnalysisTable`s; `to_parquet()` writes the long-form metric frame, large tables as `<path>.<table>.parquet` sidecars. Markdown matches the Appendix B shape minus the regression column (regressions are Phase 2).
- [ ] **Step 3: Commit.** `feat(goldenanalysis): report exporters (json/markdown/parquet)`

---

## Phase 1.7 — CLI

### Task 1.7.1: `goldenanalysis report`

**Files:**
- `packages/python/goldenanalysis/goldenanalysis/cli/__init__.py`
- `packages/python/goldenanalysis/goldenanalysis/cli/main.py`
- `packages/python/goldenanalysis/tests/test_cli.py`

- [ ] **Step 1 (red):** with `typer.testing.CliRunner`, invoke `report <fixture.parquet> --analyzers frame.summary --format markdown`; assert exit 0 and stdout contains `frame.row_count`. Also `--format json` emits valid JSON parseable into `AnalysisReport`. Run red.
- [ ] **Step 2 (green):** Typer `app` with a `report` command (input path → load via polars if `.parquet`/`.csv`, else treat as a JSON report and re-render). `--analyzers` (comma-list, default all), `--format {markdown,json}` default markdown, `--out` optional. `trend`/`regressions` subcommands are **stubs** this phase that exit with a clear "available in 0.2.0 (ReportHistory)" message — keep the surface visible but honest.
- [ ] **Step 3:** verify the console script resolves: `uv run goldenanalysis report packages/python/goldenanalysis/tests/fixtures/customers_small.parquet --analyzers frame.summary`.
- [ ] **Step 4: Commit.** `feat(goldenanalysis): CLI (report; trend/regressions stubbed)`

---

## Phase 1.8 — Native loader stub (always falls back)

### Task 1.8.1: `core/_native_loader.py`

**Files:**
- `packages/python/goldenanalysis/goldenanalysis/core/_native_loader.py`
- `packages/python/goldenanalysis/tests/test_native_loader.py`

- [ ] **Step 1 (red):** with the wheel absent, `native_module()` returns `None` and `GOLDENANALYSIS_NATIVE` unset → pure path; `GOLDENANALYSIS_NATIVE="1"` → raises `RuntimeError` (require-native parity lane contract); `_GATED_ON` is an empty set. Run red.
- [ ] **Step 2 (green):** copy the structure of `packages/python/goldencheck/goldencheck/core/_native_loader.py`, env var `GOLDENANALYSIS_NATIVE` (`0`/`1`/`auto`), import order `goldenanalysis._native` → `goldenanalysis_native._native` → `None`, `_GATED_ON: set[str] = set()`. No call site uses it yet — it exists so Phase 4 has the gate ready and the contract is under test from day one.
- [ ] **Step 3: Commit.** `feat(goldenanalysis): native loader gate stub (pure fallback)`

---

## Phase 1.9 — Parity fixture (the Appendix B report)

### Task 1.9.1: Commit the canonical report fixture

**Files:**
- `packages/python/goldenanalysis/tests/fixtures/report_frame_summary.json`
- `packages/python/goldenanalysis/tests/test_report_schema.py`

- [ ] **Step 1 (red):** `test_report_schema.py` runs `analyze(fixture_df, ["frame.summary"])`, strips volatile fields (`generated_at`, `run_id` — reuse a `_strip_volatile` helper), and asserts byte-equality against `report_frame_summary.json`. This is the file the future TS port (Phase 3) will assert against too. Run red.
- [ ] **Step 2 (green):** generate the fixture from the analyzer output once, hand-verify the values against Appendix A, commit it. Lock it: the test now guards every future change to `frame.summary` output.
- [ ] **Step 3: Commit.** `test(goldenanalysis): canonical frame.summary report fixture (P/TS parity anchor)`

---

## Phase 1.10 — Docs & package metadata

### Task 1.10.1: README, CHANGELOG, server.json, golden-suite.json

**Files:**
- `packages/python/goldenanalysis/README.md`
- `packages/python/goldenanalysis/CHANGELOG.md`
- `packages/python/goldenanalysis/server.json`
- `packages/python/goldenanalysis/golden-suite.json`

- [ ] **Step 1:** README with quickstart (`pip install goldenanalysis` → `ga.analyze(df)` + CLI), and a **"GoldenCheck vs GoldenAnalysis"** disambiguation section (read-only / consumes-other-stages / cross-run framing per the spec). Mermaid sideband diagram optional (single-line node labels per root CLAUDE.md).
- [ ] **Step 2:** `CHANGELOG.md` with a `0.1.0` entry. `server.json` + `golden-suite.json` copied from goldenpipe and re-pointed (`io.github.benseverndev-oss/goldenanalysis`).
- [ ] **Step 3: Commit.** `docs(goldenanalysis): README, CHANGELOG 0.1.0, server/golden-suite manifests`

---

## Phase 1.11 — CI wiring, full green, push

### Task 1.11.1: Path-filter + matrix inclusion

**Files:** `.github/workflows/ci.yml`

- [ ] **Step 1:** In the `changes` job `filters:` block add `python_goldenanalysis: ['packages/python/goldenanalysis/**']`. Confirm the Python dynamic-matrix emit step (the `set_python` step, ~`ci.yml:155`) includes the new package name when its filter is true — follow exactly how `goldenpipe` is wired (grep `python_goldenpipe` and mirror every occurrence).
- [ ] **Step 2:** Seed the package conservatively in the pytest matrix case statement: no `--ignore` entries needed (small green suite), `continue-on-error: true` like its siblings.
- [ ] **Step 3:** Do NOT add `publish-goldenanalysis.yml` in this task — Python publish is its own follow-up once 0.1.0 is tagged (and per CLAUDE.md must derive version from the git tag). Note it in the PR description as the immediate next step.
- [ ] **Step 4: Commit.** `ci(goldenanalysis): path filter + python matrix inclusion`

### Task 1.11.2: Full local verification

- [ ] **Step 1:** Clean run.

```bash
uv sync
uv run pytest packages/python/goldenanalysis -q
uv run ruff check packages/python/goldenanalysis
uv run goldenanalysis report packages/python/goldenanalysis/tests/fixtures/customers_small.parquet --analyzers frame.summary
```

Expected: all tests pass, ruff clean, CLI prints a markdown report.

- [ ] **Step 2:** Verify zero-dep claim — in a throwaway venv with ONLY `goldenanalysis` core installed (no goldenmatch/check/flow/pipe), `ga.analyze(df, ["frame.summary"])` works.

```bash
python -m venv /tmp/ga-clean && /tmp/ga-clean/bin/pip install -q packages/python/goldenanalysis
/tmp/ga-clean/bin/python -c "import polars as pl, goldenanalysis as ga; print(ga.analyze(pl.DataFrame({'a':[1,1,None]}), ['frame.summary']).metrics[0])"
```

- [ ] **Step 3: Push** the branch (retry with backoff per repo git policy):

```bash
for i in 1 2 3 4; do git push -u origin claude/goldencheck-rust-expansion-NmjWl && break || sleep $((2**i)); done
```

---

## Acceptance (Phase 1 done when)

Mirrors spec acceptance §1–§4, §7–§9 scoped to the frame path:

- [ ] `uv run pytest packages/python/goldenanalysis` green; package enters the CI Python matrix; doc-only changes to it skip code jobs.
- [ ] `ga.analyze(df, analyzers=["frame.summary"])` works with no other suite package installed (verified in clean venv).
- [ ] `frame.summary` emits the exact Appendix A metrics with correct `direction`; locked by the `report_frame_summary.json` fixture.
- [ ] `AnalysisReport` round-trips JSON ↔ object ↔ Markdown ↔ Parquet; `schema_version == 1`.
- [ ] Native loader contract under test (absent → fallback; `=1` → raises; `_GATED_ON` empty).
- [ ] README has the GoldenCheck-vs-GoldenAnalysis disambiguation; `CHANGELOG.md` at `0.1.0`; `server.json` + `golden-suite.json` present.

**Explicitly deferred to later phases (do not let them creep into the Phase 1 PR):** suite adapters & the other three analyzers (Phase 2), `ReportHistory`/regression/narrative (Phase 2), TS port (Phase 3), Rust `analysis-core`/`analysis-native` (Phase 4), GoldenPipe stage + goldensuite-mcp surfacing (Phase 5), the `publish-goldenanalysis*.yml` triplet (follow-up once 0.1.0 is tagged).

## Risks specific to execution

- **Editable-install entry-point flakiness** (CLAUDE.md): the registry's hard-coded fallback map (Task 1.3.1) is the mitigation; keep it in sync with the entry-points table.
- **Fixture CWD** (CLAUDE.md): every fixture read anchored to `__file__`, or it passes locally and fails in CI.
- **`pytest -n auto` isolation** (CLAUDE.md): no cross-test registration assumptions.
- **Parquet dep weight:** pyarrow is already in the suite graph; if a fresh `goldenanalysis`-only install pulls an unexpectedly heavy tree, gate `to_parquet` behind a clear ImportError message rather than a hard core dep — but default is core, matching the spec.
