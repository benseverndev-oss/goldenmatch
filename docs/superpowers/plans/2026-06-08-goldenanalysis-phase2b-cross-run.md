# GoldenAnalysis Phase 2b — Cross-Run (ReportHistory + Regression Detection) Plan

> **For agentic workers:** REQUIRED: Use superpowers:executing-plans (inline, on this box — subagents can't run pytest here) to implement this plan. Steps use checkbox (`- [ ]`) syntax. Each task is TDD-shaped: failing test → red → minimal impl → green → commit.

**Goal:** Ship the GoldenAnalysis cross-run layer — `ReportHistory` (append-only store of `AnalysisReport`s), `trend()` over a metric, `detect_regressions()` with a `RegressionPolicy`, narrative generation, and wiring the `trend`/`regressions` CLI from their Phase-1 stubs to real implementations.

**Architecture:** `ReportHistory` mirrors goldenmatch's `IdentityStore` persistence idiom (`backend=`/`path=`/`connection=` constructor; a `SCHEMA_VERSION` + helpers) but defaults to **JSONL** (an append-only reports log is the natural fit) with **SQLite** optional — both stdlib, no new deps. Regression detection is pure functions over the stored reports; the `Baseline` strategy and per-metric `RegressionPolicy` are the two decisions the spec's worked scenario forced. Narrative is a template over the flagged regressions + the largest co-moving metrics.

**Tech Stack:** Python ≥3.11, pydantic v2, stdlib `json`/`sqlite3`/`statistics`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-08-goldenanalysis-cross-cutting-analysis-engine-design.md` — the `ReportHistory` / `Baseline` / `RegressionPolicy` blocks, the 5 decisions, and Appendix B (the regression-flagged Markdown report).

**Builds on:** Phase 2a (PR #810) — the analyzers/adapters/`analyze_*` that produce the `AnalysisReport`s this layer stores. `AnalysisReport` already carries `schema_version`, `run_id`, `source` (incl. `dataset`), `metrics` (with `direction`), `narrative`.

**Grounding — the persistence idiom to mirror** (`goldenmatch/goldenmatch/identity/store.py:125-194`): `IdentityStore(backend="sqlite", path=..., connection=None, ...)`; inline backend dispatch in `__init__`; `SCHEMA_VERSION` + `_migrate` via `PRAGMA user_version`; `_exec`/`_fetchone`/`_fetchall` helpers. Reconciliation: GoldenAnalysis's reporting store keeps the **same constructor shape** but adds `backend="jsonl"` (default) alongside `backend="sqlite"`.

---

## Conventions

- Repo root for commands. Env every local run: `POLARS_SKIP_CPU_CHECK=1`, `PYTHONIOENCODING=utf-8`. Run via `.venv/Scripts/python.exe -m pytest packages/python/goldenanalysis <path>`.
- All Phase 2b tests are **pure** (build `AnalysisReport`s in-process; no suite deps, no polars beyond what Phase 1 already pulls). Safe to run the full goldenanalysis package locally (small, fast).
- Branch `feat/goldenanalysis-phase2b-cross-run`, cut from `main` **after #810 merges**.
- TDD-shaped; `feat(goldenanalysis):` commit messages.

---

## Phase 2b.0 — Cross-run models

### Task 0.1: `Baseline`, `RegressionPolicy`, `Regression`, `TrendSeries`

**Files:**
- Create: `packages/python/goldenanalysis/goldenanalysis/models/policy.py`
- Modify: `packages/python/goldenanalysis/goldenanalysis/models/__init__.py` (export)
- Test: `packages/python/goldenanalysis/tests/test_policy_models.py`

- [ ] **Step 1 (red):** assert `RegressionPolicy(default_pct=10.0, per_metric={"match.recall_safe_bound": 2.0})` exposes `.threshold_for("match.recall_safe_bound") == 2.0` and `.threshold_for("other") == 10.0`; `Regression(metric=..., baseline=0.97, current=0.89, delta_pct=-8.2, flagged=True)` round-trips; `TrendSeries(metric_key=..., dataset=..., points=[(run_id, value), ...])` holds its points. Run red.
- [ ] **Step 2 (green):** implement in `policy.py`:
  ```python
  Baseline = Literal["previous", "rolling_median", "last_known_good"] | str  # or a pinned run_id

  class RegressionPolicy(BaseModel):
      default_pct: float = 10.0
      per_metric: dict[str, float] = Field(default_factory=dict)
      def threshold_for(self, key: str) -> float:
          return self.per_metric.get(key, self.default_pct)

  class Regression(BaseModel):
      metric: str
      baseline: float
      current: float
      delta_pct: float
      flagged: bool
      direction: Direction = "neutral"

  class TrendSeries(BaseModel):
      metric_key: str
      dataset: str
      points: list[tuple[str, float]]  # (run_id, value), oldest -> newest
  ```
- [ ] **Step 3: Commit.** `feat(goldenanalysis): cross-run models (Baseline, RegressionPolicy, Regression, TrendSeries)`

---

## Phase 2b.1 — Regression math (pure, backend-free)

### Task 1.1: `_regressions.py` — the decision logic

**Files:**
- Create: `packages/python/goldenanalysis/goldenanalysis/_regressions.py`
- Test: `packages/python/goldenanalysis/tests/test_regression_logic.py`

Pure functions over a list of `(run_id, value)` history + the current report's metrics. No storage.

- [ ] **Step 1 (red):** `test_regression_logic.py`:
  - `baseline_value(history, strategy, window)` — `"previous"` returns the last point; `"rolling_median"` returns `statistics.median` of the last `window` points; `"last_known_good"` returns the last point (v1: same as previous; documented). Assert exact values on a hand-built series `[0.97,0.96,0.98,0.97,0.97,0.96,0.97]` → rolling_median(window=7)=0.97, previous=0.97.
  - `is_regression(metric, baseline, current, policy)` — respects `direction`: a `higher_better` metric flags only on a DROP beyond the per-metric pct; `lower_better` flags only on a RISE; `neutral` flags on either direction beyond pct. Assert the worked scenario: `match.recall_safe_bound` (H) 0.97→0.89 (-8.2%) flags under a 2% gate; the SAME drop does NOT flag under default 10%; a +3% wobble on a default-gate metric does not flag; `cluster.singleton_ratio` (·) 0.58→0.71 (+22.4%) flags under 10%.
  - Determinism: same inputs → same output.
  Run red.
- [ ] **Step 2 (green):** implement. `delta_pct = (current - baseline) / baseline * 100` (guard baseline==0). Flag rule:
  - `higher_better`: flag if `delta_pct <= -threshold`
  - `lower_better`: flag if `delta_pct >= +threshold`
  - `neutral`: flag if `abs(delta_pct) >= threshold`
- [ ] **Step 3: Commit.** `feat(goldenanalysis): regression decision logic (baseline strategy + direction-aware policy)`

---

## Phase 2b.2 — `ReportHistory` (JSONL + SQLite)

### Task 2.1: JSONL backend + the store surface

**Files:**
- Create: `packages/python/goldenanalysis/goldenanalysis/history.py`
- Modify: `goldenanalysis/__init__.py` (lazy export `ReportHistory`)
- Test: `packages/python/goldenanalysis/tests/test_history_jsonl.py`

- [ ] **Step 1 (red):** with `ReportHistory(backend="jsonl", path=tmp_path/"a.jsonl")`:
  - `append(report)` then `append` a second report (same `dataset`, different `run_id`) → `reports("customers")` returns both in append order.
  - keying is `(analysis_name, dataset, run_id)`: appending two reports with the SAME triple keeps the latest (idempotent upsert) — assert `len(reports(...)) == 1` after a re-append of the same run_id.
  - `trend("cluster.singleton_ratio", "customers", last_n=14)` returns a `TrendSeries` whose points are the metric's value per run in order.
  - `detect_regressions("customers", baseline="rolling_median", policy=...)` over a seeded 8-run history flags the `match.recall_safe_bound` 2%-gate drop and ignores the noise wobble (the spec acceptance §6 scenario).
  - `baseline="previous"` over a post-step pair flags nothing.
  Run red.
- [ ] **Step 2 (green):** implement `ReportHistory.__init__(backend="jsonl", path=".golden/analysis.jsonl", connection=None, database="goldenanalysis")` mirroring IdentityStore's dispatch:
  - `backend=="jsonl"`: `path` is the append-only file; `append` writes one JSON line `{analysis_name, dataset, run_id, schema_version, recorded_at, report: <AnalysisReport JSON>}`; reads parse all lines and **last-wins** per `(analysis_name, dataset, run_id)`. `analysis_name` defaults to `"default"` (a future multi-analysis seam); `dataset` from `report.source["dataset"]`.
  - `reports(dataset, analysis_name="default")` → list[AnalysisReport] in recorded order.
  - `trend(metric_key, dataset, last_n=30)` → pull each report's metric value (skip reports lacking it), last `last_n`, as `TrendSeries`.
  - `detect_regressions(dataset, baseline="rolling_median", window=7, policy=None, analysis_name="default")`: the LATEST report is "current"; the prior reports are the history. For each metric in current, compute `baseline_value` over the prior series + `is_regression`. Return the flagged `Regression`s (carry `direction` from the metric).
  - Raise `NotImplementedError` for unknown backends (mirror IdentityStore).
- [ ] **Step 3: Commit.** `feat(goldenanalysis): ReportHistory jsonl backend + trend + detect_regressions`

### Task 2.2: SQLite backend (optional, same surface)

**Files:**
- Modify: `goldenanalysis/history.py`
- Test: `packages/python/goldenanalysis/tests/test_history_sqlite.py`

- [ ] **Step 1 (red):** the SAME assertions as Task 2.1 but with `backend="sqlite", path=tmp_path/"a.db"`. Plus: a second `ReportHistory` opened on the same path sees the persisted reports (durability). Run red.
- [ ] **Step 2 (green):** add the `sqlite` branch: `sqlite3.connect`, `PRAGMA journal_mode=WAL`, a `analysis_reports` table `(report_id TEXT PRIMARY KEY, analysis_name, dataset, run_id, schema_version, recorded_at, payload TEXT, UNIQUE(analysis_name,dataset,run_id))`, `SCHEMA_VERSION=1` via `PRAGMA user_version`. `append` = `INSERT OR REPLACE`. Reuse the pure trend/regression logic over the rows. Mirror IdentityStore's `_exec`/`_fetchone`/`_fetchall` shape (sqlite-only here).
- [ ] **Step 3: Commit.** `feat(goldenanalysis): ReportHistory sqlite backend (durable, same surface)`

---

## Phase 2b.3 — Narrative generation

### Task 3.1: `narrative.py` — co-moving metrics + flagged regressions

**Files:**
- Create: `packages/python/goldenanalysis/goldenanalysis/narrative.py`
- Test: `packages/python/goldenanalysis/tests/test_narrative.py`

- [ ] **Step 1 (red):** `build_narrative(report, regressions)` returns a one-paragraph string that: names the worst flagged regression (largest `abs(delta_pct)` on a `flagged` one) with its baseline→current + gate; lists the top co-moving metrics. On the Appendix-B inputs (recall_safe_bound flagged -8.2% gate 2%, singleton_ratio +22.4%, findings +795), assert the string contains `"recall safe-bound"`/`"0.89"`/`"2%"` and `"singleton"`/`"0.71"` and `"email_blanked"` if that table is present. With **no** regressions, returns a neutral summary (the largest-magnitude metrics) — no "regression" wording. Run red.
- [ ] **Step 2 (green):** implement a deterministic template. Rank flagged regressions by `abs(delta_pct)`; co-moving = the other flagged/large-delta metrics. ASCII only (Windows-terminal safe; no em-dash). Pull a `findings_by_class` top class if present.
- [ ] **Step 3: Commit.** `feat(goldenanalysis): narrative generation (flagged regressions + co-moving metrics)`

### Task 3.2: Markdown regression callout (Δ column)

**Files:**
- Modify: `goldenanalysis/render.py`
- Test: extend `tests/test_exporters.py`

- [ ] **Step 1 (red):** `format_markdown(report, regressions=[...])` (new optional arg) prepends a `> ⚠️ **N regression(s) flagged.** ...` callout and adds a `Δ vs baseline` column to the metric table for metrics present in `regressions` (🔴 on flagged). Without `regressions`, output is byte-identical to Phase 1 (the existing 4 exporter tests stay green). Run red.
- [ ] **Step 2 (green):** thread an optional `regressions` arg through `format_markdown` (default `None` → current behavior). `AnalysisReport.to_markdown(regressions=None)` passes it through.
- [ ] **Step 3: Commit.** `feat(goldenanalysis): markdown regression callout + delta column`

---

## Phase 2b.4 — Wire the CLI from stubs

### Task 4.1: real `trend` / `regressions` commands

**Files:**
- Modify: `goldenanalysis/cli/main.py`
- Test: extend `tests/test_cli.py`

- [ ] **Step 1 (red):** with a seeded `ReportHistory` on a tmp path, `goldenanalysis trend --metric cluster.singleton_ratio --dataset customers --history <path> --last 14` exits 0 and prints the series; `goldenanalysis regressions --dataset customers --history <path> --baseline rolling_median` exits 0 and prints the flagged regressions (or "no regressions"). A `--policy` option (JSON or `key=pct,...`) sets per-metric gates. Run red (the stubs currently exit 1).
- [ ] **Step 2 (green):** replace the two stub bodies. Open `ReportHistory(backend=..., path=history)` (infer backend from the path suffix: `.db`→sqlite else jsonl). `trend` → `hist.trend(...)` rendered as a small table; `regressions` → `hist.detect_regressions(...)` rendered. Keep exit code 1 ONLY when `regressions` finds flagged ones AND `--fail-on-regression` is passed (CI-gate ergonomics); otherwise 0.
- [ ] **Step 3: Commit.** `feat(goldenanalysis): wire trend/regressions CLI to ReportHistory (0.2.0)`

---

## Phase 2b.5 — Acceptance fixture + docs + verify

### Task 5.1: the worked-scenario regression fixture

**Files:**
- Test: `packages/python/goldenanalysis/tests/test_scenario_regression.py`

- [ ] **Step 1 (red):** reconstruct the spec's Maya scenario end to end with hand-built reports: seed 7 nights of healthy reports (`recall_safe_bound≈0.97`, `singleton_ratio≈0.58`) + the 8th regressed night (0.89 / 0.71 / findings 1205), append all to a `ReportHistory`, run `detect_regressions(..., policy={match.recall_safe_bound: 2.0})`, assert it flags `match.recall_safe_bound` (which a global 10% gate would miss) and `cluster.singleton_ratio`, that `baseline="previous"` over the post-step pair flags nothing, and that `build_narrative` names the root-cause chain. This is acceptance §6. Run red, then green once 2b.1-2b.3 land.
- [ ] **Step 2: Commit.** `test(goldenanalysis): worked-scenario regression acceptance (spec acceptance §6)`

### Task 5.2: docs + verify + push

**Files:** `CHANGELOG.md`, `README.md`

- [ ] **Step 1:** CHANGELOG 0.2.0 — add the cross-run section. README: a "Cross-run" snippet (`ReportHistory`, `detect_regressions`, the CLI). Remove the "stubs until 0.2.0" caveat (now real).
- [ ] **Step 2:** `uv run pytest packages/python/goldenanalysis -q` green; `ruff check` clean; bounded `pyright` on the package source 0 errors.
- [ ] **Step 3: Push** `feat/goldenanalysis-phase2b-cross-run` (auth dance), open PR vs main.

---

## Acceptance (Phase 2b done when)

- [ ] `ReportHistory` (jsonl default + sqlite optional) keyed by `(analysis_name, dataset, run_id)`; same constructor idiom as `IdentityStore`; durable on sqlite.
- [ ] `detect_regressions` with `rolling_median` flags a `match.recall_safe_bound` drop under a per-metric 2% gate that a global 10% gate misses, ignores a 3% noise wobble, respects `Metric.direction`, and is deterministic. `baseline="previous"` over a post-step pair flags nothing.
- [ ] `trend` returns an ordered `TrendSeries`; the CLI `trend`/`regressions` are real (no stubs), with `--policy` + `--fail-on-regression`.
- [ ] Narrative names the worst flagged regression + co-moving metrics; the Markdown report shows the regression callout + Δ column; no-regression path is byte-identical to Phase 1.
- [ ] ruff + pyright clean; full goldenanalysis suite green locally; no new dependencies.

### Explicitly deferred (later phases)
TS port of the cross-run layer (Phase 3), Rust accelerator (Phase 4), the GoldenPipe terminal stage + goldensuite-mcp surfacing + publish workflows (Phase 5). `last_known_good` is v1-aliased to `previous` (a real "last green run" needs a health signal — a documented follow-up).
