# GoldenCheck Polars eviction — Stage-2 S2.0 (nopolars scaffold + advisory lane) + Stage-2 roadmap

Date: 2026-07-10
Status: design — approved in brainstorming, pending spec review
Base: fresh `origin/main` (relation front R1–R4 complete: all profilers seam-routed or declined; `polars>=1.0` still a HARD base dep; no nopolars lane yet)
Parent program: goldencheck Polars eviction (see the P0 design + the R1 relation roadmap for the profiler fronts)

## Context

The profiler fronts are done — every GoldenCheck profiler is seam-routed (column P0–A2b; relation
R1–R3) or explicitly declined (relation R4). The seam (`core/frame.py`) still has exactly ONE backend,
`PolarsColumn`/`PolarsFrame`, delegating every op to Polars. Stage-2 is the program that lets the scan
path run **without Polars installed**.

**Reframe from the exploration (goldenflow is the proven template).** The sibling `goldenflow` package
already completed this eviction. It did NOT write a pure-Python Polars clone. It kept the single Polars
backend, added a **covered-subset executor** on the native Rust kernel (+ a pure-Python-list fallback),
**declined anything uncovered back to Polars** (byte-identical or Polars, never wrong), shipped a
`tests/nopolars/` module + an **advisory** CI lane that physically uninstalls polars, then flipped
`polars → [polars]` extra. Two facts make goldencheck's Stage-2 a multi-piece program:
- A byte-identical pure-Python backend for the hard seam ops (`str_match_count`/`str_filter`/
  `str_replace_all` via Rust regex, `str_to_date` via chrono, `value_counts_desc` tie-order, `dtype`
  inference) is infeasible in stdlib Python.
- `goldencheck-native` has the relational/statistical kernels (FD, composite keys, Benford, near-dup,
  denial) but **no string/regex/date/value_counts kernels** — so a covered backend needs NEW Rust
  kernels (goldenflow's native crate already had them; goldencheck's doesn't).

This spec designs **S2.0** — the smallest real first piece — and locks the Stage-2 roadmap.

## The Stage-2 roadmap (locked here)

| Piece | What | Notes |
|---|---|---|
| **S2.0** (this spec) | `tests/nopolars/` + a locally-provable import-survival test + an advisory `goldencheck_nopolars` CI lane that pip-uninstalls polars. | No non-Polars backend, no covered scan yet. Proves import-survival + clean decline; builds the lane infra. Mirrors goldenflow 4f (minus the covered scan goldencheck can't run yet). |
| **S2.1** | A covered non-Polars `Column`/`Frame` for the mechanical ops (len/null_count/n_unique/min/max/sum/is_null/eq/gt_mask/fill_null/filter_by/slice/get/…) → the *simple* column profilers (nullability/cardinality/uniqueness) run polars-free byte-identically; decline the regex/date/value_counts ones. Adds the covered-scan assertions to the lane. | Medium. First real backend. |
| **S2.2** | New `goldencheck-native` kernels for the hard ops (str_match/str_replace/str_to_date/value_counts) — Rust regex = byte-identical to Polars' regex crate — widening coverage. | Large (Rust). |
| **Reader + P4** | Non-Polars CSV/parquet reader → the deps-flip `polars → [polars]` (+ base dep `goldencheck-native`). | Large; the ~185MB weight payoff lands HERE, not before. |

## Scope (S2.0 only)

### In scope
1. A **locally-provable import-survival test** (extends the existing subprocess-based
   `tests/test_import_no_polars.py`): a subprocess installs a `sys.meta_path` finder that makes
   `polars` unimportable, then asserts `import goldencheck` still succeeds, `polars` stays out of
   `sys.modules`, a public entry point exists, and touching the lazy proxy raises a clean
   `ModuleNotFoundError`. Runs in the NORMAL (ci-required) suite — proves the central claim locally even
   though polars is installed.
2. A **`tests/nopolars/` module** (`__init__.py` + `test_polars_absent.py`) mirroring goldenflow's 4f:
   imports polars NOWHERE; `skipif`'d when polars is present; asserts import-survival + public entry
   points + a clean `ModuleNotFoundError` on the uncovered tail. **No covered scan** (goldencheck has no
   non-Polars backend yet — that arrives with S2.1).
3. An **advisory** `goldencheck_nopolars` CI lane in `.github/workflows/ci.yml`: a `changes`-job output +
   filter block (goldencheck paths + the workflow file) + a job that `uv pip uninstall polars`, verifies
   it's gone, and runs `pytest tests/nopolars --no-sync --noconftest` (`--noconftest` a defensive
   parity carry-over — goldencheck has no `tests/conftest.py` today). NOT in `ci-required`.
4. This roadmap doc.

### Explicitly NOT in scope
Any non-Polars `Column`/`Frame` backend (S2.1); any covered scan; a native-kernel build step in the lane
(none needed — no covered scan); new Rust kernels (S2.2); the reader; the deps-flip. No product-code
change beyond the tests + CI + doc.

### Success criteria
- `test_import_no_polars.py` gains the import-blocker test; it passes in the normal suite.
- `tests/nopolars/` collects and is `skipif`-skipped locally (polars present); the module imports polars
  nowhere.
- The `goldencheck_nopolars` CI job is wired (changes-output + filter + `if:` gate), the ci.yml still
  parses (no 0-job startup failure — the `feedback_ci_yaml_startup_failure` trap), and is advisory.
- Full suite green; `import goldencheck` still loads zero Polars.

## Design details

### 1. Import-blocker test (in `tests/test_import_no_polars.py`, alongside the existing gate)
A new `test_goldencheck_survives_polars_unimportable()` in the SAME subprocess style as the existing
`test_import_goldencheck_does_not_load_polars` (same `PYTHONPATH`-anchoring + `POLARS_SKIP_CPU_CHECK`
env). The subprocess code:
- Inserts at `sys.meta_path[0]` a `MetaPathFinder` whose `find_spec(name, ...)` raises
  `ModuleNotFoundError` for `name == "polars"` or `name.startswith("polars.")`, returns `None` otherwise.
- `import goldencheck` (must not raise — the lazy proxy defers `import polars`).
- `assert "polars" not in sys.modules`.
- `assert hasattr(goldencheck, "scan_dataframe")` (a public entry point survives).
- `from goldencheck._polars_lazy import pl; then accessing `pl.DataFrame` must raise
  `ModuleNotFoundError` (the deferred import fires and is blocked) — asserted via try/except.
- The test asserts the subprocess `returncode == 0` (like the existing gate).

This proves import-survival + clean-decline WITHOUT uninstalling polars, so it runs everywhere.

### 2. `tests/nopolars/test_polars_absent.py` (mirror goldenflow, no covered scan)
- Module docstring explains: this is the polars-genuinely-absent proof; it imports polars nowhere; it is
  `skipif`'d out where polars is present, so it only executes in the `goldencheck_nopolars` lane (or any
  polars-absent local run). NOTE it asserts import-survival + clean-decline only — the covered-scan
  assertions arrive with S2.1 (goldencheck has no non-Polars backend yet).
- `_HAS_POLARS = importlib.util.find_spec("polars") is not None`;
  `pytestmark = pytest.mark.skipif(_HAS_POLARS, reason="polars-absent proof — only runs where polars is NOT installed (the S2.0 lane)")`.
- `test_import_goldencheck_without_polars()`: `import goldencheck`; `assert "polars" not in sys.modules`;
  assert the public entry points exist (`scan_dataframe`, `scan_file`, `read_file`,
  `functional_dependencies`, `Finding`, `Severity` — importable as `goldencheck.<name>` /
  `hasattr(goldencheck, ...)`).
- `test_uncovered_path_raises_clean_error_without_polars()`:
  `from goldencheck._polars_lazy import pl`; `with pytest.raises(ModuleNotFoundError): _ = pl.DataFrame`.
- `tests/nopolars/__init__.py` empty (mirrors goldenflow's package marker).

### 3. The advisory CI lane (`.github/workflows/ci.yml`)
Mirror goldenflow's `goldenflow_nopolars` job, SIMPLER (no rust toolchain / native build — S2.0 has no
covered scan). Three edits, matching the existing ci.yml conventions (per goldencheck's CLAUDE.md CI
path-filter rules):
1. In the `changes` job `outputs:` map, add `goldencheck_nopolars: ${{ steps.filter.outputs.goldencheck_nopolars }}`.
2. In the `filter:` step's `filters:` block, add a `goldencheck_nopolars:` entry with paths:
   `packages/python/goldencheck/goldencheck/**`, `packages/python/goldencheck/tests/nopolars/**`,
   `packages/python/goldencheck/pyproject.toml`, and the workflow file itself is already globally
   force-all on ci.yml changes.
3. A new job:
   ```yaml
     goldencheck_nopolars:
       needs: changes
       if: needs.changes.outputs.goldencheck_nopolars == 'true' || needs.changes.outputs.force_all == 'true'
       runs-on: ubuntu-latest
       timeout-minutes: 20
       steps:
         - uses: actions/checkout@... (pin to the SHA the rest of ci.yml uses)
         - uses: astral-sh/setup-uv@... (same pin) with enable-cache + cache-dependency-glob
         - run: uv sync --all-packages
         - name: Uninstall polars (simulate the P4 base-deps flip)
           run: uv pip uninstall polars polars-runtime-32 polars-runtime-64 || true
         - name: Confirm polars is gone
           run: uv run --no-sync python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('polars') is None else 'polars still present')"
         - name: Polars-absent proof (tests/nopolars)
           run: uv run --no-sync python -m pytest packages/python/goldencheck/tests/nopolars --noconftest -v
   ```
   `--no-sync` so uv doesn't reinstall polars before the run. `--noconftest` is a **defensive parity
   carry-over** from goldenflow's lane: goldencheck currently has NO `tests/conftest.py` (verified), so
   `--noconftest` is a harmless no-op today; it keeps the lane robust if a polars-importing conftest is
   ever added above `tests/nopolars/`. The nopolars tests use only builtin fixtures, so `--noconftest`
   never removes anything they need.
   Use the EXACT action SHA pins the surrounding ci.yml already uses (do not introduce new/unpinned
   actions). Advisory: do NOT add `goldencheck_nopolars` to any `ci-required` / required-status
   aggregation job.

### 4. Roadmap doc
This file. Records the S2.0–S2.2/reader/P4 decomposition, the byte-identity wall, the goldenflow mirror,
and the weight-payoff-only-at-P4 reality.

## Testing / verification

- Local: `pytest tests/test_import_no_polars.py -v` (the new blocker test passes with polars installed);
  `pytest tests/nopolars -v` collects and reports the two tests as SKIPPED (polars present); the
  `tests/nopolars` module imports polars nowhere (grep). Full goldencheck suite green.
- CI (the real polars-absent proof) runs in the advisory `goldencheck_nopolars` lane — per the standing
  "arm auto-merge, don't poll CI" rule, the lane is armed and not watched; being advisory, an UNSTABLE
  there does not block the merge.
- **ci.yml must still parse** — after editing, validate it's valid YAML and the `changes` job still emits
  all outputs (the `feedback_ci_yaml_startup_failure` trap: a malformed ci.yml yields 0 jobs and blocks
  the PR looking like a slow queue).

## Risks

- **`import goldencheck` might fail polars-absent** — if any module accessed `pl.<attr>` at import time,
  the blocker/lane would surface it. The existing import gate proves no eager `pl` LOAD, so import
  should survive; if S2.0 finds otherwise, that's a real P4 bug caught early (the point of the lane).
- **ci.yml wiring** — the highest-risk part. Must add the `changes` output + filter + job consistently,
  reuse existing action SHAs, keep it advisory, and re-validate the YAML (0-job trap). Follow the
  monorepo CLAUDE.md's CI-path-filter section exactly.
- **`--noconftest` is defensive, not load-bearing** — goldencheck has NO `tests/conftest.py` today
  (unlike goldenflow, whose conftest imports polars), so `--noconftest` is a harmless no-op carried over
  for parity + future-robustness. The nopolars tests use only builtin fixtures, so it never removes
  anything they need.
- **No covered scan yet** — S2.0 deliberately proves less than goldenflow's lane (no reduced scan). This
  is honest: goldencheck has no non-Polars backend until S2.1. Documented in the module docstring + here.

## Non-goals (YAGNI)
The S2.1 backend; any covered scan; native build in the lane; new Rust kernels; the reader; the deps-flip;
a `force_all`/required-status change (the lane is advisory).
