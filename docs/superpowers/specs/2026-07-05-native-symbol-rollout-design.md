# Native-symbol gate rollout (goldencheck / goldenanalysis / goldenflow) — design

**Status:** approved (brainstorm 2026-07-05), pending spec review
**Context:** Project 1 (#1459) shipped the native-symbol reconciliation gate for
goldenmatch (reconcile host kernel references vs `wrap_pyfunction!` exports; FAIL on
referenced-but-not-registered — the #688 silent-fallback class). This rolls it to
the other native packages. Related: `project_688_stale_native_wheel`,
`project_api_parity_gate` (same gate-shape family).

## 1. What the rollout hits (investigated)

The other native packages are **not** uniform — registration style and host idiom
both vary:

| Package | Crate (symbols) | Registration | Host idiom |
| --- | --- | --- | --- |
| goldencheck | `goldencheck-native` (7) | `mod::fn` | `native_module().X` (9 refs) |
| goldenanalysis | `analysis-native` (2) | **bare `fn`** | `native_module().X` (2 refs) |
| goldenflow | `native-flow` (74) | `mod::fn` | **static import** `from ..._native import X` |
| goldenpipe | `goldenpipe-native` (5) | **bare `fn`** | **none** (reference-mode only) |

**goldenpipe is N/A and gets documented, not gated.** Its `_native` binding is a
**reference-mode parity oracle**, not a runtime accelerator (its `_native_loader.py`
says so verbatim): the pure-Python planner (`core/_planner_json.py`, pure Python,
imports `engine.resolver`/`router`) IS the runtime; the kernel exists only so the
planner **parity gate (#1424)** can compare byte-identity. goldenpipe has zero
host-accelerated references → no silent-slow-fallback to guard → the reconciliation
gate does not apply, and drift is already caught by the parity gate.

## 2. Goal

Gate **goldencheck**, **goldenanalysis**, **goldenflow** with the native-symbol
reconciliation gate, via two small scanner changes + REGISTRY entries. Document
goldenpipe as reference-mode-N/A. Per-package bootstrap must be clean (`missing`=∅).

## 3. Design (`scripts/check_native_symbols.py`)

### 3.1 Scanner change 1 — `_WRAP` regex accepts bare fn names

Current `_WRAP = re.compile(r"wrap_pyfunction!\(\s*(?:\w+::)+(\w+)")` requires **≥1**
`mod::` prefix. analysis-native + goldenpipe-native (and any future crate) register
bare: `wrap_pyfunction!(histogram, m)`. Fix to **zero-or-more**:
```python
_WRAP = re.compile(r"wrap_pyfunction!\(\s*(?:\w+::)*(\w+)")
```
Strictly more permissive; the 3 `mod::` crates are unaffected (the final `::`-segment
is still captured). Add a fixture to guard both forms.

### 3.2 Scanner change 2 — per-package reference idiom (`runtime` | `literal`)

`scan_references` currently only does the "runtime" idiom (`native_module()`/
`_ensure_native()`/bound-alias `.X`, `getattr`, `hasattr` with a **string-literal**
arg). goldencheck + goldenanalysis use exactly this idiom. goldenflow does NOT —
verified: its kernel references are **string literals**, not attribute access.

**goldenflow's real idiom (investigated).** `transforms/_native.py` is a pure-Python
**shim** (74 wrapper functions `phone_e164_native`, …); the per-transform files
import those *wrappers*, not kernel symbols. The actual kernel-symbol references live
inside `_native.py` as **quoted `*_arrow` literals** passed to runner helpers /
assigned to a variable then `getattr`'d: `_kernel_runner("phone_e164_arrow")`,
`attr = "split_address_arrow"; getattr(nm, attr)`. Because `attr` is a *variable*, the
runtime getattr-literal capture cannot see it — the reference is only visible as the
string literal. And the crate's 74 symbols are uniformly `*_arrow`-suffixed.

Add a per-package `idiom` key to REGISTRY: `"runtime"` (default; goldenmatch/
goldencheck/goldenanalysis) or `"literal"` (goldenflow):
- **runtime idiom**: unchanged (the existing `scan_file_refs`).
- **literal idiom**: in files containing the loader token, capture string literals
  matching a package-configured `literal_pattern` (goldenflow: `r'"(\w+_arrow)"'`) —
  the quoted kernel-symbol names. These reconcile 1:1 against the registered
  `*_arrow` exports. (The `static_import` idea from the prior draft is DROPPED — it
  matched the wrapper namespace, not the kernel, and produced 100% missing.)

The `run(package)` flow (parse registrations → scan references → reconcile → FAIL on
missing / REPORT unwired, with the zero-referenced fail-loud guard) is unchanged; it
consults `spec["idiom"]` to pick the reference scanner.

### 3.3 REGISTRY entries

```python
"goldencheck": {
    "crate_reg": ["packages/rust/extensions/goldencheck-native/src/lib.rs"],
    "py_root": "packages/python/goldencheck/goldencheck",
    "loader_tokens": ("native_module", "_ensure_native"),
    "idiom": "runtime",
    "allow": "parity/native_symbols/goldencheck.allow",
},
"goldenanalysis": {
    "crate_reg": ["packages/rust/extensions/analysis-native/src/lib.rs"],
    "py_root": "packages/python/goldenanalysis/goldenanalysis",
    "loader_tokens": ("native_module", "_ensure_native"),
    "idiom": "runtime",
    "allow": "parity/native_symbols/goldenanalysis.allow",
},
"goldenflow": {
    "crate_reg": ["packages/rust/extensions/native-flow/src/lib.rs"],
    "py_root": "packages/python/goldenflow/goldenflow",
    "loader_tokens": ("native_module",),   # _native.py imports native_module from the loader
    "idiom": "literal",
    "literal_pattern": r'"(\w+_arrow)"',   # goldenflow kernel symbols are *_arrow, quoted in _native.py
    "allow": "parity/native_symbols/goldenflow.allow",
},
```
(goldenmatch keeps `"idiom": "runtime"` — add the key explicitly for clarity.)

### 3.4 goldenpipe — documented, not gated

A comment block in `check_native_symbols.py` (near REGISTRY) records: goldenpipe's
`goldenpipe-native` is a reference-mode parity oracle (per its `_native_loader.py`),
not host-wired, so the reconciliation gate is intentionally not applied — planner
drift is covered by the goldenpipe parity gate (#1424). No REGISTRY entry, no CI
lane. (Also note in the PR / a one-line CLAUDE.md mention.)

## 4. Bootstrap

Run each of the 3 gates locally (box-safe source parse). **Computed ahead (spec
review):**
- **goldencheck** — `missing`=∅ (clean); `unwired` = `{functional_dependency_holds}`
  (1 dead export — `functional_dependency.py` calls `discover_functional_dependencies`).
- **goldenanalysis** — `missing`=∅, `unwired`=∅ (registered {histogram, quantile} ==
  referenced).
- **goldenflow** — with the `literal` idiom, referenced ≈ the 74 `*_arrow` literals in
  `_native.py`, reconciling 1:1 against the 74 registered → `missing`=∅. Verify the
  referenced count is ~74 (not a handful) — a small count means the `literal_pattern`
  or file-filter is wrong (the under-scan hazard).

`unwired` is REPORT-only; record counts in the PR. Empty `.allow` files at bootstrap.
Any non-empty `missing` is a real finding to triage (typo / renamed literal /
getattr-fallback to unbuilt symbol).

## 5. Testing

Extend `scripts/test_native_symbols.py` (box-safe, pure data):
- `parse_registrations_text` fixture with a **bare** `wrap_pyfunction!(foo, m)` and a
  `mod::bar` — both captured.
- a `literal` fixture: a file with `_kernel_runner("phone_e164_arrow")` +
  `attr = "split_address_arrow"` → `{phone_e164_arrow, split_address_arrow}`; a file
  without the loader token → ∅; a non-matching literal (`"not_a_kernel"`) → not
  captured by the `_arrow` pattern.
- the idiom dispatch: a `runtime` package uses the alias scanner; a `literal`
  package uses the literal-pattern scanner over loader-token files.
- Per-package real-gate smoke: `run("goldencheck")`/`("goldenanalysis")`/
  `("goldenflow")` exit 0 (missing=∅) after bootstrap.

## 6. CI

Extend the `native_symbols` job (from #1459). Two viable shapes — pick the one that
matches the existing job:
- if it's a single goldenmatch invocation, generalize to a small matrix
  `[goldenmatch, goldencheck, goldenanalysis, goldenflow]` running
  `check_native_symbols.py <pkg>`, and broaden the paths-filter to each package's
  crate (`packages/rust/extensions/{goldencheck-native,analysis-native,native-flow}/**`)
  + host (`packages/python/{goldencheck,goldenanalysis,goldenflow}/**`) +
  `scripts/check_native_symbols.py` + `parity/native_symbols/**`.
- Box-safe (source parse, no cargo build) → cheap; run the gate unit tests once.

## 7. Rollout / docs

- Single PR, branch `feat/native-symbol-rollout` off `origin/main`. Scanner changes
  + REGISTRY + 3 `.allow` files + goldenpipe doc + tests + CI. benzsevern gh;
  merge-queue → `gh pr merge --auto --squash` (no `--delete-branch`); arm + stop.
- Run **ruff** on the touched Python (the #1451 lesson).
- CLAUDE.md: one line under the native section that the gate now covers 3 more
  packages + goldenpipe is reference-mode-N/A.

## 8. Risks

- **goldenflow `literal` under-scan** — the highest risk: if the `literal_pattern`
  or file-filter is wrong the referenced set shrinks → falsely green (misses drift),
  not red. Mitigation: §4 asserts referenced ≈ 74 (a handful means it's wrong); §5
  fixture; the zero-references guard catches total failure. The `_arrow` suffix is
  goldenflow-specific (all 74 symbols carry it today) — if a future goldenflow kernel
  symbol lacks `_arrow`, the pattern misses it; the `unwired` report (that symbol
  showing as exported-but-unreferenced) surfaces the gap.
- **`literal` over-capture** — a stray `"\w+_arrow"` string in a loader-token file
  that isn't a kernel symbol would false-RED (referenced-not-registered). Verified
  none today (the `*_arrow` literals in `_native.py` are all kernel names); if one
  appears, the `.allow` file is the escape hatch.
- **Regex over-permissiveness** — `(?:\w+::)*` could in theory match a
  `wrap_pyfunction!(` inside a comment with a bare word; the existing behavior
  already tolerated this for `mod::` (a commented token without `(` and a name won't
  match). Bare-name comments are unlikely in registration files; the fixture covers
  the real forms.
- **goldenpipe assumption** — documented as reference-mode from its own loader
  docstring + zero host refs; if a future change wires goldenpipe-native into the
  runtime host, add a REGISTRY entry then (the doc comment says so).
