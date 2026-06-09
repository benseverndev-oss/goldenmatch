# GoldenAnalysis Phase 2a — Suite Adapters + Analyzers Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking. Each task is TDD-shaped: write the failing test, run it red, write the minimal implementation, run it green, commit.

**Goal:** Ship the GoldenAnalysis suite-consumption layer — `match.rates`, `cluster.distribution`, `quality.rollup` analyzers + the `match`/`check`/`flow`/`pipe` artifact adapters + `analyze_match()` / `analyze_pipeline()` — so an `AnalysisReport` can be produced from real GoldenMatch / GoldenCheck / GoldenFlow / GoldenPipe outputs.

**Architecture:** Analyzers are **pure functions over `AnalyzerInput.artifacts`** (a dict of typed producer outputs); adapters are the only code that touches a suite package, and they lazy-import it so the core install stays dependency-light. Analyzers degrade gracefully — they emit the metrics their available artifacts support and silently omit the rest. The cross-run layer (`ReportHistory`, regressions, narrative) is **Phase 2b, not here**.

**Tech Stack:** Python ≥3.11, polars, pydantic v2; optional `goldenmatch` / `goldencheck` / `goldenflow` / `goldenpipe` (the `[match]`/`[check]`/`[flow]`/`[pipe]` extras).

**Spec:** `docs/superpowers/specs/2026-06-08-goldenanalysis-cross-cutting-analysis-engine-design.md` (Appendix A = metric catalog; the worked scenario = the integration target).

**Builds on:** Phase 1 (`goldenanalysis 0.1.0`, PR #808). This plan assumes the Phase 1 package exists: `models/`, `core/aggregate.py`, `analyzers/base.py` + `frame_summary.py`, `registry.py`, `adapters/frame.py`, `_api.analyze`, exporters, CLI.

---

## Grounding: the REAL producer APIs (verified 2026-06-08)

These are the actual shapes the adapters consume. **Do not trust the spec's illustrative shapes over these.**

**GoldenMatch** (`goldenmatch/_api.py:67-95`): `dedupe_df(df, config=None, ...) -> DedupeResult` with fields `golden`, `clusters: dict[int, dict]`, `dupes`, `unique`, `stats: dict`, `scored_pairs: list[tuple[int,int,float]]` (**retained by default**, `(min_id, max_id, score)`), `config`, `postflight_report`, `memory_stats`.
- `stats` keys: `total_records`, `total_clusters`, `matched_records`, `match_rate`.
- `clusters` per-entry: multi-member `{members: list[int], size: int, oversized: bool, pair_scores: dict, confidence: float, bottleneck_pair, cluster_quality: str}`; singleton `{members, size, oversized}`.
- **Recall certificate is NOT on `DedupeResult`** and **NOT re-exported**. It lives in `goldenmatch/core/recall_certificate.py`: `RecallEstimate{recall, ...}` (unsupervised point estimate) and `RecallCertificate{recall, recall_lower (SAFE bound), recall_upper, ...}`. `match.rates` therefore treats the certificate as an **optional input** the adapter passes in (see Task 2).
- `match_df(...) -> MatchResult{matched, unmatched, stats, ...}` — NO clusters/scored_pairs.

**GoldenCheck** (`goldencheck/engine/scanner.py`): `scan_dataframe(df) -> (findings, profile)` — **true in-memory, no file path**. `Finding` (`goldencheck/models/finding.py:14-26`) `{severity: Severity(INFO=1/WARNING=2/ERROR=3), column, check: str, message, affected_rows, confidence, source, metadata}`. `profile.health_score(findings_by_column, errors, warnings) -> (grade: str, score: int 0-100)` is a **method on `DatasetProfile`** (no top-level `health_score`). Also `cell_quality(df) -> dict[(row,col), float]`, `functional_dependencies(df) -> list[FunctionalDependency]`.

**GoldenFlow** (`goldenflow/engine/transformer.py:17-20`): `transform_df(df) -> TransformResult{df, manifest}`. `Manifest{source, records: list[TransformRecord], errors, created_at}`; `TransformRecord{column, transform, affected_rows, total_rows, ...}`. So `flow.rows_changed = sum(r.affected_rows for r in records)` (derived), `flow.rules_fired = len(records)`.

**GoldenPipe** (`goldenpipe/models/context.py:52-90`): `PipeResult{status, source, input_rows, stages, artifacts: dict, skipped, errors, reasoning, timing}`. `artifacts` real keys: `findings` (list[dict]), `profile`, `manifest` (GoldenFlow Manifest), `clusters` (dict[int,dict]), `golden`/`unique`/`dupes`, `match_stats` (dict), `scored_pairs` (list[tuple]), `matchkey_used` (str), `identity_summary` (dict), `conflicts`. **No `recall_certificate` key** unless the prerequisite GoldenMatch PR (below) adds it.

---

## The `AnalyzerInput.artifacts` convention (the contract this plan establishes)

Phase 1's `AnalyzerInput` already has `dataset: str`, `frame: Any`, `artifacts: dict[str, Any]`. This plan standardizes the artifact keys (mirroring `PipeResult.artifacts` so the `pipe` adapter is a near-passthrough):

| key | value | produced by |
|---|---|---|
| `clusters` | `dict[int, dict]` | match, pipe |
| `scored_pairs` | `list[tuple[int,int,float]]` | match, pipe |
| `match_stats` | `dict` (total_records, match_rate, ...) | match, pipe |
| `match_threshold` | `float \| None` (primary matchkey threshold from config) | match, pipe |
| `recall_certificate` | `dict{estimate: float\|None, safe_bound: float\|None}` (normalized) | match (if supplied), pipe (if present) |
| `findings` | `list[Finding \| dict]` | check, pipe |
| `profile` | `DatasetProfile \| None` | check, pipe |
| `manifest` | GoldenFlow `Manifest \| dict` | flow, pipe |

Analyzers read these keys and **degrade**: emit the metrics the present keys support, omit the rest.

---

## Prerequisite (separate, named PR — NOT part of this plan's branch)

**`feat(goldenmatch): surface unsupervised recall estimate on DedupeResult + pipe artifact`** — opt-in `certify: bool = False` on `dedupe_df` that, when set, computes `estimate_recall(build_decorrelated_systems(config.matchkeys))` after clustering and attaches `result.recall_certificate` (a `RecallEstimate`); the `goldenmatch.dedupe` pipe stage surfaces it as `artifacts["recall_certificate"]`. The **audit-calibrated safe bound stays evaluate-only** (it needs labels). This makes `match.recall_estimate` real end-to-end; `match.recall_safe_bound` remains an optional passed-in input. **This plan does not depend on that PR** — `match.rates` consumes the cert optionally and degrades — but the headline scenario only becomes real once it lands.

---

## Conventions

- Commands run from the repo root. Env for every local run: `POLARS_SKIP_CPU_CHECK=1`, `PYTHONIOENCODING=utf-8`. Use `.venv/Scripts/python.exe -m pytest <path>` (Windows; `uv run pytest` misses workspace members per packages/python/CLAUDE.md).
- **Local runs touch the pure tests only** (`test_match_rates.py`, `test_cluster_dist.py`, `test_quality_rollup.py`, `test_adapters_unit.py`, `test_analyze_suite.py`). The `@requires_extra` integration tests import `goldenmatch`/`goldencheck`/etc. and run **in CI only** (importing those locally risks the zombie-python box starvation documented in MEMORY). Do NOT run them locally.
- TDD-shaped: failing test → red → minimal impl → green → commit. `feat(goldenanalysis):` / `test(goldenanalysis):` messages.
- Branch: `feat/goldenanalysis-phase2a-suite-adapters`, cut from `main` AFTER #808 (Phase 1) merges. If #808 hasn't merged, stack on its branch and rebase post-merge (heed the squash-merge stacked-PR gotcha in CLAUDE.md).

---

## Phase 2a.0 — Artifact convention + shared report assembly

### Task 0.1: Refactor `_api` to share report assembly

**Files:**
- Modify: `packages/python/goldenanalysis/goldenanalysis/_api.py`
- Test: `packages/python/goldenanalysis/tests/test_assemble.py`

Phase 1's `analyze()` inlines metric/table concatenation + run_id stamping. Extract it so `analyze_match`/`analyze_pipeline` reuse it.

- [ ] **Step 1 (red):** `test_assemble.py` — import a new `_assemble_report(inp, analyzer_names, *, run_id=None, generated_at=None)` from `goldenanalysis._api`; feed an `AnalyzerInput(frame=pl.DataFrame({"a":[1]}), dataset="d")` and `["frame.summary"]`; assert it returns an `AnalysisReport` with `analyzers_run == ["frame.summary"]`, `source["dataset"]=="d"`, and a `frame.row_count` metric. Run red (ImportError).
- [ ] **Step 2 (green):** Extract the body of `analyze()` (the loop that resolves analyzers, runs each over `inp`, concatenates `metrics`/`tables`, records unavailable, stamps `run_id`/`source`) into `_assemble_report(inp, analyzer_names, *, run_id=None, generated_at=None) -> AnalysisReport`. `source` = `{"dataset": inp.dataset, "producer": inp.artifacts.get("__producer__", "frame")}`; carry `source["unavailable"]` for requested-but-undiscoverable names. Re-implement `analyze(df, analyzers=None, ...)` to build the `frame` `AnalyzerInput` then call `_assemble_report`. **All 4 existing `test_analyze.py` tests must still pass unchanged.**
- [ ] **Step 3 (green):** Run `test_assemble.py` + `test_analyze.py`. Expected: all pass.
- [ ] **Step 4: Commit.** `refactor(goldenanalysis): extract _assemble_report for reuse across analyze entrypoints`

---

## Phase 2a.1 — `match.rates` analyzer (pure)

### Task 1.1: `match.rates` over hand-built artifacts

**Files:**
- Create: `packages/python/goldenanalysis/goldenanalysis/analyzers/match_rates.py`
- Test: `packages/python/goldenanalysis/tests/test_match_rates.py`

Metrics (Appendix A): `match.pair_count` (pairs, ·), `match.match_rate` (ratio, ·), `match.threshold` (score, ·), `match.recall_estimate` (ratio, H), `match.recall_safe_bound` (ratio, H), `match.mean_pair_score` (score, ·); table `score_histogram`.

- [ ] **Step 1 (red):** `test_match_rates.py`:
  ```python
  from goldenanalysis.analyzers.match_rates import MatchRatesAnalyzer
  from goldenanalysis.models import AnalyzerInput

  def _input(**artifacts):
      return AnalyzerInput(dataset="customers", artifacts=artifacts)

  def test_core_metrics():
      inp = _input(
          scored_pairs=[(0, 1, 0.9), (1, 2, 0.8), (3, 4, 0.95)],
          match_stats={"total_records": 10, "match_rate": 0.3, "total_clusters": 2, "matched_records": 3},
          match_threshold=0.82,
      )
      m = {x.key: x for x in MatchRatesAnalyzer().run(inp).metrics}
      assert m["match.pair_count"].value == 3
      assert m["match.match_rate"].value == 0.3
      assert m["match.threshold"].value == 0.82
      assert abs(m["match.mean_pair_score"].value - (0.9 + 0.8 + 0.95) / 3) < 1e-9
      assert "match.recall_estimate" not in m   # no cert supplied -> omitted
      assert "match.recall_safe_bound" not in m

  def test_recall_from_certificate():
      inp = _input(
          scored_pairs=[(0, 1, 0.9)],
          match_stats={"total_records": 4, "match_rate": 0.5},
          recall_certificate={"estimate": 0.94, "safe_bound": 0.89},
      )
      m = {x.key: x for x in MatchRatesAnalyzer().run(inp).metrics}
      assert m["match.recall_estimate"].value == 0.94
      assert m["match.recall_estimate"].direction == "higher_better"
      assert m["match.recall_safe_bound"].value == 0.89
      assert m["match.recall_safe_bound"].direction == "higher_better"

  def test_score_histogram_table():
      inp = _input(scored_pairs=[(0,1,0.1),(2,3,0.9)], match_stats={"total_records": 4, "match_rate": 0.5})
      tables = {t.name: t for t in MatchRatesAnalyzer().run(inp).tables}
      assert "score_histogram" in tables

  def test_empty_pairs_degrades():
      inp = _input(scored_pairs=[], match_stats={"total_records": 5, "match_rate": 0.0})
      m = {x.key: x for x in MatchRatesAnalyzer().run(inp).metrics}
      assert m["match.pair_count"].value == 0
      assert "match.mean_pair_score" not in m  # no pairs -> omit
  ```
  Run red.
- [ ] **Step 2 (green):** implement. Read `cert = inp.artifacts.get("recall_certificate")`; normalize with a helper accepting a dict `{estimate, safe_bound}` OR a dataclass (`getattr(cert, "recall", None)` for estimate, `getattr(cert, "recall_lower", None)` for safe_bound). Emit `match.recall_estimate`/`match.recall_safe_bound` only when the respective value is not None. `match.threshold` only when `match_threshold` present. `match.mean_pair_score` only when pairs non-empty. `score_histogram` via `goldenanalysis.core.aggregate.histogram([s for *_, s in scored_pairs], bins=10)` → `AnalysisTable(name="score_histogram", columns=["bin_left","count"], rows=[[edge,c] for edge,c in hist])`. `info = AnalyzerInfo(name="match.rates", consumes=["scored_pairs","match_stats"], produces=[...])`.
- [ ] **Step 3 (green):** run `test_match_rates.py`. Expected: pass.
- [ ] **Step 4: Commit.** `feat(goldenanalysis): match.rates analyzer (pairs, match_rate, recall cert, score histogram)`

---

## Phase 2a.2 — `cluster.distribution` analyzer (pure)

### Task 2.1: `cluster.distribution` over a hand-built cluster dict

**Files:**
- Create: `packages/python/goldenanalysis/goldenanalysis/analyzers/cluster_dist.py`
- Test: `packages/python/goldenanalysis/tests/test_cluster_dist.py`

Metrics: `cluster.count`, `cluster.record_count`, `cluster.singleton_ratio`, `cluster.size_p50`/`size_p95`/`size_max`, `cluster.reduction_ratio`; table `cluster_size_histogram` (buckets `1,2,3,"4+"`).

- [ ] **Step 1 (red):** `test_cluster_dist.py` — build `clusters = {0:{"members":[0],"size":1}, 1:{"members":[1],"size":1}, 2:{"members":[2,3,4],"size":3}, 3:{"members":[5,6],"size":2}}` (4 clusters; sizes [1,1,3,2]; records=7). Assert: `cluster.count==4`, `cluster.record_count==7`, `cluster.singleton_ratio==0.5`, `cluster.size_max==3`, `cluster.reduction_ratio == 1 - 4/7`, and a `cluster_size_histogram` table with rows `[[1,2],[2,1],[3,1],["4+",0]]`. Run red.
- [ ] **Step 2 (green):** implement reading `clusters = inp.artifacts["clusters"]`; `sizes = [c["size"] for c in clusters.values()]`; quantiles via `aggregate.quantile(sizes, 0.5/0.95)`; `record_count = sum(sizes)` (fall back to `match_stats["total_records"]` if clusters absent but stats present — but if `clusters` absent the analyzer emits nothing). `reduction_ratio = 1 - len(clusters)/record_count` (guard record_count==0). Histogram buckets: counts of size==1/2/3 and size>=4 → rows `[[1,n1],[2,n2],[3,n3],["4+",n4]]`. `info` consumes `["clusters"]`. Directions all neutral except none H/L (per Appendix A, all `·`).
- [ ] **Step 3 (green):** run. Expected pass.
- [ ] **Step 4: Commit.** `feat(goldenanalysis): cluster.distribution analyzer (sizes, singletons, reduction, histogram)`

---

## Phase 2a.3 — `quality.rollup` analyzer (pure, degrades per-producer)

### Task 3.1: `quality.rollup` over findings + manifest

**Files:**
- Create: `packages/python/goldenanalysis/goldenanalysis/analyzers/quality_rollup.py`
- Test: `packages/python/goldenanalysis/tests/test_quality_rollup.py`

Metrics: `quality.findings_total` (findings, L), `quality.columns_with_findings` (columns, L), `quality.score` (ratio, H, only if a `profile` is present), `flow.rows_changed` (rows, ·), `flow.rules_fired` (count, ·); table `findings_by_class`.

- [ ] **Step 1 (red):** `test_quality_rollup.py` uses **dicts** for findings (the `PipeResult.artifacts` shape) and a duck-typed manifest:
  ```python
  from types import SimpleNamespace
  from goldenanalysis.analyzers.quality_rollup import QualityRollupAnalyzer
  from goldenanalysis.models import AnalyzerInput

  FINDINGS = [
      {"severity": "WARNING", "column": "email", "check": "email_blanked"},
      {"severity": "WARNING", "column": "email", "check": "email_blanked"},
      {"severity": "ERROR", "column": "phone", "check": "phone_unparseable"},
  ]
  MANIFEST = SimpleNamespace(records=[
      SimpleNamespace(column="email", transform="blank_malformed", affected_rows=1188, total_rows=4000),
      SimpleNamespace(column="phone", transform="e164", affected_rows=12, total_rows=4000),
  ])

  def test_quality_and_flow_metrics():
      inp = AnalyzerInput(dataset="customers", artifacts={"findings": FINDINGS, "manifest": MANIFEST})
      r = QualityRollupAnalyzer().run(inp)
      m = {x.key: x for x in r.metrics}
      assert m["quality.findings_total"].value == 3
      assert m["quality.columns_with_findings"].value == 2     # email, phone
      assert m["flow.rows_changed"].value == 1200              # 1188 + 12
      assert m["flow.rules_fired"].value == 2
      assert "quality.score" not in m                          # no profile supplied
      tbl = {t.name: t for t in r.tables}["findings_by_class"]
      rows = {row[0]: row[1] for row in tbl.rows}
      assert rows["email_blanked"] == 2 and rows["phone_unparseable"] == 1

  def test_degrades_findings_only():
      inp = AnalyzerInput(dataset="d", artifacts={"findings": FINDINGS})
      m = {x.key: x for x in QualityRollupAnalyzer().run(inp).metrics}
      assert "quality.findings_total" in m and "flow.rows_changed" not in m

  def test_degrades_manifest_only():
      inp = AnalyzerInput(dataset="d", artifacts={"manifest": MANIFEST})
      m = {x.key: x for x in QualityRollupAnalyzer().run(inp).metrics}
      assert "flow.rules_fired" in m and "quality.findings_total" not in m
  ```
  Run red.
- [ ] **Step 2 (green):** implement with field accessors that handle dict OR object: `_get(f, "column")` = `f[k] if isinstance(f, dict) else getattr(f, k)`; severity normalized to upper-string. From findings: `findings_total=len`, `columns_with_findings=len({col})`, `findings_by_class` = Counter of `check`. `quality.score` only if `inp.artifacts.get("profile")` is not None: call `profile.health_score(findings_by_column=<{col:{"errors":e,"warnings":w}}>)[1] / 100.0` (build the per-column severity counts from findings). From manifest: `records = manifest.records if not dict else manifest["records"]`; `flow.rows_changed = sum(_get(r,"affected_rows"))`, `flow.rules_fired = len(records)`. Emit only the groups whose source artifact is present. `info` consumes `["findings","manifest"]`.
- [ ] **Step 3 (green):** run. Expected pass.
- [ ] **Step 4: Commit.** `feat(goldenanalysis): quality.rollup analyzer (findings + flow stats, per-producer degrade)`

---

## Phase 2a.4 — Adapters (lazy-import producers)

### Task 4.1: `match` adapter (DedupeResult → AnalyzerInput) — duck-typed test

**Files:**
- Create: `packages/python/goldenanalysis/goldenanalysis/adapters/match.py`
- Test: `packages/python/goldenanalysis/tests/test_adapters_unit.py`

- [ ] **Step 1 (red):** in `test_adapters_unit.py`, feed `MatchArtifactAdapter().load(result, dataset="customers", certificate={"estimate":0.94,"safe_bound":0.89})` a `SimpleNamespace(clusters={0:{"members":[0],"size":1}}, scored_pairs=[(0,1,0.9)], stats={"total_records":2,"match_rate":0.5}, config=None)`. Assert the returned `AnalyzerInput.dataset=="customers"` and `.artifacts` has `clusters`, `scored_pairs`, `match_stats` (== result.stats), `recall_certificate=={"estimate":0.94,"safe_bound":0.89}`, and `__producer__=="goldenmatch"`. Run red.
- [ ] **Step 2 (green):** implement `MatchArtifactAdapter.load(self, result, *, dataset=None, certificate=None) -> AnalyzerInput`. It reads `result.clusters/scored_pairs/stats` (duck-typed; no `goldenmatch` import). `match_threshold` extracted from `result.config` if it exposes matchkeys with a threshold (best-effort try/except → None). `recall_certificate`: if `certificate` arg given, normalize to `{estimate, safe_bound}` (accept dict or `RecallEstimate`/`RecallCertificate` via getattr `recall`/`recall_lower`); else if `getattr(result, "recall_certificate", None)` present (the prerequisite GoldenMatch PR), normalize that; else omit. `artifacts["__producer__"]="goldenmatch"`.
- [ ] **Step 3 (green):** run. Expected pass.
- [ ] **Step 4: Commit.** `feat(goldenanalysis): match adapter (DedupeResult -> artifacts, optional recall cert)`

### Task 4.2: `flow` + `check` adapters

**Files:**
- Create: `packages/python/goldenanalysis/goldenanalysis/adapters/flow.py`
- Create: `packages/python/goldenanalysis/goldenanalysis/adapters/check.py`
- Test: extend `tests/test_adapters_unit.py`

- [ ] **Step 1 (red):** `FlowArtifactAdapter().load(SimpleNamespace(df=<pl df>, manifest=<SimpleNamespace records=[...]>), dataset="d")` → `AnalyzerInput.artifacts["manifest"]` is the manifest, `frame` is the transformed df, `__producer__=="goldenflow"`. For check: `CheckArtifactAdapter().load(df)` requires the `goldencheck` import — test it raises a clear `ImportError`-derived message when goldencheck is absent (use `monkeypatch` to force the lazy import to fail) OR mark this assertion `@requires_extra`. Test the pure path: `CheckArtifactAdapter().from_scan(findings=[...], profile=None, dataset="d")` (a constructor that takes pre-computed scan output, no goldencheck needed) → artifacts has `findings`, `profile`, `__producer__=="goldencheck"`. Run red.
- [ ] **Step 2 (green):** `FlowArtifactAdapter.load(result, *, dataset=None)` reads `result.df` + `result.manifest` (duck-typed). `CheckArtifactAdapter` has two entry points: `from_scan(findings, profile, *, dataset=None)` (pure, no import) and `load(df, *, dataset=None, **scan_kwargs)` which lazy-imports `goldencheck`, calls `goldencheck.scan_dataframe(df, **scan_kwargs)`, then delegates to `from_scan`. The lazy import raises `RuntimeError("goldenanalysis[check] requires goldencheck: pip install goldenanalysis[check]")` on ImportError.
- [ ] **Step 3 (green):** run pure tests. Expected pass.
- [ ] **Step 4: Commit.** `feat(goldenanalysis): flow + check adapters (lazy producer import, pure from_scan seam)`

### Task 4.3: `pipe` adapter (PipeResult → AnalyzerInput)

**Files:**
- Create: `packages/python/goldenanalysis/goldenanalysis/adapters/pipe.py`
- Test: extend `tests/test_adapters_unit.py`

- [ ] **Step 1 (red):** `PipeArtifactAdapter().load(SimpleNamespace(artifacts={"clusters":{...},"scored_pairs":[...],"match_stats":{...},"findings":[...],"manifest":<ns>,"recall_certificate":<ns recall=0.94 recall_lower=0.89>}, source="customers.parquet", input_rows=4000))` → `AnalyzerInput.dataset=="customers"` (stem of source), `.artifacts` carries the same keys through, `recall_certificate` normalized to `{estimate:0.94, safe_bound:0.89}`, `__producer__=="goldenpipe"`. Run red.
- [ ] **Step 2 (green):** implement. `dataset` = `Path(result.source).stem` (or "frame" for `<DataFrame>`). Copy `result.artifacts` into `artifacts` (shallow), normalize any `recall_certificate` to the `{estimate, safe_bound}` dict, set `__producer__="goldenpipe"`. No `goldenpipe` import needed (duck-typed).
- [ ] **Step 3 (green):** run. Expected pass.
- [ ] **Step 4: Commit.** `feat(goldenanalysis): pipe adapter (PipeResult.artifacts passthrough + cert normalize)`

---

## Phase 2a.5 — `analyze_match` / `analyze_pipeline` + registration

### Task 5.1: entry points

**Files:**
- Modify: `packages/python/goldenanalysis/goldenanalysis/_api.py`
- Modify: `packages/python/goldenanalysis/goldenanalysis/__init__.py` (lazy export)
- Test: `packages/python/goldenanalysis/tests/test_analyze_suite.py`

- [ ] **Step 1 (red):** `test_analyze_suite.py` builds a duck-typed dedupe result + calls `ga.analyze_match(result, dataset="customers")`; asserts `analyzers_run` contains `match.rates` and `cluster.distribution`, metrics include `match.pair_count` and `cluster.count`, `source["dataset"]=="customers"`. A second test builds a duck-typed pipe result (artifacts with findings+manifest+clusters) and calls `ga.analyze_pipeline(result)`; asserts `analyzers_run` is the subset whose artifacts are present (e.g. `quality.rollup`, `match.rates`, `cluster.distribution`) and NOT analyzers whose artifacts are absent. Run red.
- [ ] **Step 2 (green):** `analyze_match(result, *, dataset=None, certificate=None, run_id=None)` = `MatchArtifactAdapter().load(...)` then `_assemble_report(inp, ["match.rates","cluster.distribution"])`. `analyze_pipeline(result, *, run_id=None)` = `PipeArtifactAdapter().load(result)` then select analyzers whose `info.consumes` is satisfied by present artifact keys (`_artifact_compatible(inp)` helper: an analyzer runs iff at least one of its `consumes` keys is in `inp.artifacts`), then `_assemble_report`. Lazy-export both from `__init__._LAZY`.

  > **Known limitation (by design; full Appendix-B parity is a 2b concern):** `frame.summary` will NOT fire under `analyze_pipeline` because `PipeResult` does **not** expose the input DataFrame (confirmed: `PipeContext.df` is not on `PipeResult`; only `input_rows` is). So `analyze_pipeline`'s `analyzers_run` is the suite-analyzer subset, not including `frame.summary`. The Appendix-B example report (which lists `frame.summary` + a `frame.row_count`) is the **parity-fixture target deferred to Phase 2b alongside narrative** — if it's wanted, 2b can emit `frame.row_count` from `result.input_rows` without the frame. Do not expand 2a to chase it.
- [ ] **Step 3 (green):** run. Expected pass.
- [ ] **Step 4: Commit.** `feat(goldenanalysis): analyze_match + analyze_pipeline entrypoints`

### Task 5.2: register the 3 analyzers

**Files:**
- Modify: `packages/python/goldenanalysis/pyproject.toml` (`[project.entry-points."goldenanalysis.analyzers"]`)
- Modify: `packages/python/goldenanalysis/goldenanalysis/registry.py` (`_FALLBACK`)
- Test: extend `tests/test_registry.py`

- [ ] **Step 1 (red):** assert `available_analyzers()` ⊇ `{"frame.summary","match.rates","cluster.distribution","quality.rollup"}` and each `load_analyzer(name).info.name == name`. Run red.
- [ ] **Step 2 (green):** add the 3 entry points + fallback map entries (`match.rates`→`analyzers.match_rates:MatchRatesAnalyzer`, etc.). Re-run `uv pip install -e packages/python/goldenanalysis` so the entry-points refresh.
- [ ] **Step 3 (green):** run. Expected pass.
- [ ] **Step 4: Commit.** `feat(goldenanalysis): register match.rates/cluster.distribution/quality.rollup`

---

## Phase 2a.6 — Integration tests (CI-only, require extras)

### Task 6.1: real-producer end-to-end (marked, no local run)

**Files:**
- Create: `packages/python/goldenanalysis/tests/conftest.py` add `requires_extra` marker helper
- Create: `packages/python/goldenanalysis/tests/integration/test_real_producers.py`

- [ ] **Step 1:** add a `requires(pkg)` skip helper in `conftest.py`: `requires_goldenmatch = pytest.mark.skipif(importlib.util.find_spec("goldenmatch") is None, reason="needs goldenanalysis[match]")` etc.
- [ ] **Step 2 (CI-green):** `test_real_producers.py`:
  - `@requires_goldenmatch` build a tiny synthetic dedupe fixture (surnames spread across soundex codes per MEMORY `feedback_synthetic_surname_fixtures`), run `goldenmatch.dedupe_df`, `ga.analyze_match(result, dataset="t")`, assert `cluster.count >= 1` and `match.pair_count >= 0` and the report round-trips to markdown.
  - `@requires_goldencheck @requires_goldenflow` run `scan_dataframe` + `transform_df` on a messy frame, hand-assemble a `quality.rollup` input via the adapters, assert `quality.findings_total >= 0` and `flow.rules_fired >= 0`.
  - `@requires_goldenpipe` run `goldenpipe.run_df`/`run`, `ga.analyze_pipeline(result)`, assert ≥2 analyzers ran.
  - Each integration test carries a loud comment: these prove the adapters against REAL shapes; a producer shape change breaks them here, not silently.
- [ ] **Step 3: Commit.** `test(goldenanalysis): real-producer integration (CI-only, require extras)`

> **Do NOT run these locally** (imports goldenmatch/polars → box risk). They run in the CI lane wired in Task 7.1.

---

## Phase 2a.7 — CI wiring + docs

### Task 7.1: install extras in the goldenanalysis CI lane

**Files:** `.github/workflows/ci.yml`

- [ ] **Step 1:** In the `python` job, after `uv sync --all-packages`, add a goldenanalysis-only step mirroring the goldenmatch datafusion pattern:
  ```yaml
  - if: matrix.pkg == 'goldenanalysis'
    run: uv pip install -e 'packages/python/goldenanalysis[match,check,flow,pipe]'
  ```
  so the `@requires_*` integration tests actually RUN (the sibling packages resolve via the workspace). Without this they silently skip — a false green (the goldenmatch CLAUDE.md importorskip lesson).
- [ ] **Step 2:** Confirm the `python_goldenanalysis` path filter (added in Phase 1) still triggers the lane; no new filter entry needed (same package path). A change under `packages/python/goldenanalysis/**` already enters the matrix.
- [ ] **Step 3: Commit.** `ci(goldenanalysis): install suite extras so adapter integration tests run`

### Task 7.2: CHANGELOG + README

**Files:** `packages/python/goldenanalysis/CHANGELOG.md`, `README.md`

- [ ] **Step 1:** CHANGELOG `0.2.0` (unreleased) entry: suite adapters + 3 analyzers + analyze_match/analyze_pipeline. README: add a "Over a GoldenMatch result" + "Whole pipeline" snippet (mirror spec Public API). Note `recall_safe_bound` needs a labeled audit; `recall_estimate` needs the GoldenMatch `certify` prerequisite.
- [ ] **Step 2: Commit.** `docs(goldenanalysis): document suite adapters + analyze_match/analyze_pipeline (0.2.0)`

---

## Phase 2a.8 — Verify + push

### Task 8.1: full local verification (pure tests only)

- [ ] **Step 1:** `POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest packages/python/goldenanalysis -q --ignore=packages/python/goldenanalysis/tests/integration` → all pure tests green.
- [ ] **Step 2:** `.venv/Scripts/python.exe -m ruff check packages/python/goldenanalysis` → clean.
- [ ] **Step 3:** bounded pyright: `uv run --with pyright pyright packages/python/goldenanalysis/goldenanalysis` → 0 errors.
- [ ] **Step 4:** Push (auth dance: `gh auth switch --user benzsevern`, push, switch back to `benzsevern-mjh`). Open PR vs main. CI runs the integration lane (extras installed) — confirm the `@requires_*` tests actually ran (grep the log for their node ids, not "skipped").

---

## Acceptance (Phase 2a done when)

- [ ] `match.rates`, `cluster.distribution`, `quality.rollup` emit the exact Appendix-A metrics with correct `direction`; pure unit tests lock the values.
- [ ] `match`/`check`/`flow`/`pipe` adapters map real producer outputs → the standardized `artifacts` keys; core install stays zero-suite-dep (adapters lazy-import; pure `from_scan`/duck-typed seams tested without the producers).
- [ ] `analyze_match(result)` and `analyze_pipeline(result)` assemble reports; `analyze_pipeline` fans out only to analyzers whose artifacts are present.
- [ ] `match.rates` emits `recall_estimate`/`recall_safe_bound` when a certificate is supplied and **degrades silently** when not.
- [ ] The 3 analyzers are registered (entry-point + fallback); `available_analyzers()` lists all four.
- [ ] CI installs `[match,check,flow,pipe]` and the integration tests RUN (not skip) — verified in the log.
- [ ] ruff + pyright clean; pure suite green locally.

### Explicitly deferred to Phase 2b (do NOT build here)
`ReportHistory` (jsonl + sqlite), `trend`, `detect_regressions`, `RegressionPolicy`, `Baseline` strategies, **narrative generation**, wiring the `trend`/`regressions` CLI from stubs. The GoldenMatch `certify`-surfacing PR is a separate named producer PR.
