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

### 3.2 Scanner change 2 — per-package reference idiom

`scan_references` currently only does the "runtime" idiom (`native_module()`/
`_ensure_native()`/bound-alias `.X`, `getattr`, `hasattr`). goldenflow uses a
**static-import** idiom instead. Add a per-package `idiom` key to REGISTRY:
`"runtime"` (default; goldenmatch/goldencheck/goldenanalysis) or `"static_import"`
(goldenflow).

- **runtime idiom**: unchanged (the existing `scan_file_refs`).
- **static_import idiom**: scan for `from <anything>_native import <names>`,
  extracting the imported symbol names. Must handle **single-line** (`from
  goldenflow.transforms._native import build_canonical_map_native`) AND
  **multi-line parenthesised** (`from x._native import (\n  a,\n  b as c,\n)`)
  forms, and `import ... as alias` (capture the original name, left of `as`).
  A regex over the parenthesised/one-line import body, split on commas, take the
  token left of any `as`. Restrict to files whose text contains `_native import`
  (bounds false positives). Test files excluded (structurally — `py_root` is the
  package inner dir, tests live outside).

The `run(package)` flow (parse registrations → scan references → reconcile →
FAIL on missing / REPORT unwired, with the zero-referenced fail-loud guard) is
unchanged; it just consults `spec["idiom"]` to pick the reference scanner.

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
    "loader_tokens": ("_native import",),      # static-import trigger for the file filter
    "idiom": "static_import",
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

Run each of the 3 gates locally (box-safe source parse). Each must be `missing`=∅.
Triage each `missing` row if any (typo / getattr-fallback to unbuilt symbol /
static-import of an unexported name). `unwired` (exported, no host ref) is
REPORT-only — record the counts in the PR (likely surfaces dead exports, as
goldenmatch's 3 did). Empty `.allow` files at bootstrap unless a real cross-kernel
case appears. For **goldenflow** the static-import extraction is the risk — verify
the extracted reference set is non-trivial and matches the visible imports before
trusting a green result (the fail-loud zero-references guard catches a fully-broken
extractor, but a partially-broken one under-scans silently — spot-check the set).

## 5. Testing

Extend `scripts/test_native_symbols.py` (box-safe, pure data):
- `parse_registrations_text` fixture with a **bare** `wrap_pyfunction!(foo, m)` and a
  `mod::bar` — both captured.
- a `static_import` fixture: single-line + multi-line parenthesised `from
  x._native import (a, b as c)` → `{a, b}`; a `_native import`-free file → ∅.
- the idiom dispatch: a runtime-idiom package uses the alias scanner; a
  static_import package uses the import scanner.
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

- **Static-import under-scan (goldenflow)** — the highest risk: a partially-broken
  extractor under-scans → falsely green (misses drift), not falsely red. Mitigation:
  §4 spot-check the extracted set vs the visible imports; §5 multi-line fixture; the
  zero-references guard catches a total failure. If goldenflow's imports use a form
  the extractor misses, its referenced set shrinks — visible as a suspiciously small
  count for a 74-symbol crate.
- **Regex over-permissiveness** — `(?:\w+::)*` could in theory match a
  `wrap_pyfunction!(` inside a comment with a bare word; the existing behavior
  already tolerated this for `mod::` (a commented token without `(` and a name won't
  match). Bare-name comments are unlikely in registration files; the fixture covers
  the real forms.
- **goldenpipe assumption** — documented as reference-mode from its own loader
  docstring + zero host refs; if a future change wires goldenpipe-native into the
  runtime host, add a REGISTRY entry then (the doc comment says so).
