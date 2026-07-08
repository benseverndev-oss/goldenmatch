# GoldenCheck Arrow-native core foundation (Wave 0)

Date: 2026-07-08
Status: design — approved in brainstorming, pending spec review
Parent program: "All GoldenCheck compute elements driven by Arrow-native source-of-truth kernels"
(7-wave decomposition; this is Wave 0)

## Context

GoldenCheck (`packages/python/goldencheck`, v1.3.0, DQBench detect 88.40) is data-quality
validation that discovers rules from data. It already has the two-crate native split this
directive describes:

- `packages/rust/extensions/goldencheck-core/` — pyo3-free kernels (`benford`, `fuzzy`,
  `keys`), currently **slice-based and Arrow-free** (`&[f64]`, `&[u64]`; only dep
  `rustc-hash`).
- `packages/rust/extensions/goldencheck-native/` — abi3 PyO3 shim (standalone workspace,
  `arrow=55`), decodes Arrow via `PyArrowType<ArrayData>` zero-copy, extracts slices, and
  delegates to core.
- `goldencheck/core/_native_loader.py` — the loader/gate. `GOLDENCHECK_NATIVE=auto|0|1`;
  `auto` currently requires membership in the `_GATED_ON` allow-list AND a symbol probe.

Five kernels are wired and **byte/set-exact** with their pure-Python fallbacks: `benford`,
`composite_keys`, `functional_dependencies`, `fuzzy_values`, `approximate_fd`.
The `2026-07-01-rust-is-the-reference-roadmap.md` classes goldencheck as a low-risk "fast
follower" needing only the mechanical authority flip.

### The three gaps between "as-is" and "Arrow-native source-of-truth kernels"

1. **Coverage** — only 5 of ~27 compute elements have kernels; ~22 scan-path checks are
   Polars-only (the standing gate was "a kernel must beat Polars", and Polars wins there).
2. **Arrow-native-ness** — `goldencheck-core` is slice-based; the Arrow boundary lives up in
   the pyo3 `-native` crate, so core cannot back a pyo3-free DuckDB/DataFusion/WASM surface.
3. **numpy/scipy** — the baseline/drift statistical layer has no kernel equivalents.

## The larger program (context only — not this spec's scope)

The directive is "literally every element." That is far too large for one spec, so it is
decomposed into 7 waves, each with its own spec → plan → implementation cycle:

| Wave | Sub-project | Parity difficulty |
|---|---|---|
| **0** | **Arrow-native core foundation (this spec)** | Low (parity-locked) |
| 1 | Aggregate scan checks (cardinality, nullability, uniqueness, type_inference) | Low |
| 2 | Distributional/format checks (range, sequence, encoding, format, pattern, freshness) | Medium |
| 3 | Cross-column relations (age, null_correlation, numeric_cross, temporal, safe_pk, refs) | Medium |
| 4 | Baseline statistics (distribution fit, entropy, percentile, correlation, patterns, priors) | High (scipy numerics) |
| 5 | Drift detector (13 drift types; depends on Wave 4) | High |
| 6 | Semantic embeddings (model inference is not a kernel; likely stays fallback) | Special-case |

### Governing decisions (fixed in brainstorming, apply to all waves)

- **Substrate:** hand-written arrow-rs kernels; **no** query engine (no DataFusion/DuckDB
  inside core).
- **Boundary:** Arrow-in-core — kernels operate on `&dyn Array` / `ArrayRef` directly.
- **Parity/authority:** the Rust kernel is the source of truth; the current Polars/numpy
  output is a **gated regression oracle** during migration. Every divergence is reviewed and
  either accepted+documented (kernel more correct) or fixed (kernel bug). Nothing diverges
  silently. This is the "measured justification per check" the directive's collision with the
  measure-first / "beat Polars" principle requires.
- **Arrow I/O rule:** Arrow-in universally; Arrow-out **only** where the result is itself a
  column (e.g. per-row violation masks → `BooleanArray`). Scalar/aggregate reductions
  (histograms, key-sets, FD lists) return plain Rust structs.

## Wave 0 scope

### In scope

1. Add arrow-rs to `goldencheck-core`; convert the 5 existing kernels from slice-in to
   `&dyn Array`-in with internal downcast + **null-awareness**. `-native` thins to pure
   pyarrow↔`ArrayRef` marshalling.
2. Build the reusable **parity-oracle harness** (with an accepted-divergence registry).
3. Fix the `approximate_fd` two-symbol probe honesty.
4. Do the roadmap **reference-mode flip** (loader `auto` semantics, delete `_GATED_ON`, CI
   inversion to native-default lane + fallback lane).

### Explicitly NOT in scope

No *new* check kernel. Zero of the ~22 Polars-only checks are ported here. Wave 0 is pure
foundation + de-risking on parity-locked code. No DuckDB/DataFusion/WASM surface. No
numpy/scipy work. Pure-Python kernels are **not** deleted.

The two public GoldenMatch bridges — `goldencheck.cell_quality` (reuses `fuzzy_values`) and
`goldencheck.functional_dependencies` (reuses the strict + approx-FD kernels) — are consumers
of the same 5 components, not separate kernels. Wave 0 keeps their Python-facing pyarrow
signatures intact, so they are covered **transitively** by the 5-kernel parity: if the
kernels stay byte/set-exact, the bridges do too. No bridge-specific work, but the harness
should include at least one fixture exercising each bridge's public entry point to prove the
transitive coverage rather than assume it.

### Success criteria

- All 5 kernels run Arrow-in-core; native output is byte/set-identical to before (they were
  already exact — any diff is a mechanism bug, not semantics).
- The parity-oracle harness runs green with an **empty** accepted-divergence registry.
- Native is the default CI test lane; a separate `GOLDENCHECK_NATIVE=0` fallback lane stays
  independently green.
- `GOLDENCHECK_NATIVE=1` still hard-requires the wheel; `=0` still forces fallback.

## Architecture — the Arrow-in-core boundary

### a) Core dependency + signatures

`goldencheck-core/Cargo.toml` gains `arrow`, pinned to the **identical version and crate
form** `goldencheck-native` uses (the umbrella `arrow = { version = "55", default-features =
false }`). Note: `goldencheck-native` path-deps core in a single cargo build, and `ArrayData`
/ `ArrayRef` cross the boundary as concrete types — so core must resolve to the **same**
`arrow-array` tree native does. Using the umbrella `arrow` crate in both (rather than core
depending on the split `arrow-array`/`arrow-buffer`/`arrow-schema` sub-crates directly)
guarantees a single resolution and avoids the type/linker error the top risk warns about.
Kernel signatures move from typed slices to Arrow arrays with internal downcast and
null-bitmap handling:

```rust
// before: pub fn benford_leading_digits(values: &[f64]) -> [u64; 9]
// after:  pub fn benford_leading_digits(array: &dyn Array) -> Result<[u64; 9], ArrowError>
//          -> downcast Float64/Int64/Decimal, iterate, honor the null bitmap
```

This is a **correctness upgrade**, not just directive-compliance: today the caller
pre-extracts `&[f64]` and the null bitmap is lost. Arrow-in-core lets kernels see Arrow's
null buffer directly — exactly what checks like nullability (Wave 1) need.

### b) The `-native` shim thins

`-native` stops doing `ArrayData → Float64Array → slice`. It decodes
`PyArrowType<ArrayData>` → `ArrayRef` and hands `array.as_ref()` to core. The Arrow boundary
moves **down** into core (closing gap #2); native becomes pure FFI marshalling with no
business logic.

### c) Arrow I/O rule (restated)

Arrow-in for every kernel. Arrow-out only when the output is itself a column. The 5 Wave-0
kernels' outputs (benford histogram, composite-key sets, FD lists, fuzzy cluster sets,
approx-FD violation sets) are reductions → they return plain Rust structs, unchanged in
shape from today.

## The reusable parity-oracle harness

Promote the hard-coded `tests/core/test_native_parity.py` into `tests/core/parity_harness.py`.
For a registered component it:

1. Generates **random + adversarial** fixtures. Per-kernel edge cases: nulls, empty,
   single-value, powers-of-ten (benford), unicode/ties (fuzzy), near-unique determinants (FD).
2. Runs the component twice — native (`GOLDENCHECK_NATIVE=1`) and fallback
   (`GOLDENCHECK_NATIVE=0`) — on identical input.
3. Compares. Equal → pass. Divergent → look up the **accepted-divergence registry**
   (documented `{component, rationale, product-decision-ref}` entries). Registered → pass
   (expected lossy fallback). Unregistered → **fail**.

The authority model is baked in: native is the oracle; the Python fallback is asserted
"conforms or is documented-lossy." The registry is the "gated" in "gated oracle" — nothing
diverges silently.

**Why this de-risks the program:** in Wave 0 all 5 kernels are byte/set-exact, so the
registry is **empty** and the harness must be green. That proves the *harness itself* is
correct on known-exact code before Wave 1 relies on it to catch real Polars-vs-kernel
divergences. If the harness is buggy, we learn now, against code where the answer is known.

## The reference-mode flip

Four concrete edits to `_native_loader.py` + CI:

### a) `auto` semantics

```python
# before: return _native is not None and component in _GATED_ON and _has_symbol(component)
# after:  return _native is not None and _has_symbol(component)
```

### b) Delete `_GATED_ON`

A check with no kernel has no `_COMPONENT_SYMBOLS` entry → `_has_symbol` returns False →
clean fallback. `_has_symbol` **is** the gate; no allow-list needed. `mode==0` (force
fallback) and `mode==1` (require native, raise if missing) semantics are unchanged.

### c) Fix the `approximate_fd` probe

`_COMPONENT_SYMBOLS` values become tuples of *all* required symbols; `_has_symbol` requires
every one present:

```python
"approximate_fd": ("discover_approximate_fds", "fd_violation_rows"),
```

A stale wheel missing `fd_violation_rows` today still passes the single-symbol probe, runs
`discover_approximate_fds` (wasted), then `AttributeError`s on `fd_violation_rows` and falls
fully back to Python — a **silent redundant native pass**, not a crash or a wrong-output
half-run (the call site at `relations/approx_fd.py` wraps both native calls in one
`try/except -> _python(...)`). The tuple-probe fix removes the wasted pass and makes the
capability honest (the goldenmatch #688 footgun, applied honestly).

### d) CI inversion

The real CI is the **root** `.github/workflows/ci.yml` — the pre-fold
`packages/python/goldencheck/.github/workflows/test.yml` is an orphan and is silently ignored
(root `CLAUDE.md`: only root `.github/workflows/` runs). So all edits target `ci.yml`.

The current state is **not** "native untested": `ci.yml` already has a dedicated
`goldencheck_native` job that builds the ext via `scripts/build_goldencheck_native.py`, runs
the parity + relations/profilers suites with the ext present, and runs a required
`GOLDENCHECK_NATIVE=1` lane (paths-filter-gated on the core/native/loader/relations files).
The gap the flip closes is the **main test matrix**: it runs the full 550+ suite pure-Python.
Wave 0 makes that matrix **native-default** (build the ext, run with native present) and adds
a **separate** `GOLDENCHECK_NATIVE=0` fallback lane, so the default center of gravity becomes
Rust-as-oracle rather than Python. The existing `goldencheck_native` required-mode lane stays.

## Versioning + docs

- `goldencheck` — the 5 components are output-identical and the Arrow-in-core refactor is
  internal (no Python API change) → **minor** bump, no migration note.
- `goldencheck-native` crate — bump `Cargo.toml` + `pyproject.toml` `[project].version` in
  lockstep (maturin reads pyproject; `skip-existing: true` silently no-ops a stale version).
- Docs relabel (pure-Python = non-authoritative lossy fallback) via the `rollout-docs-sweep`
  skill at the end.

## Risks

- **Arrow version + crate-form lockstep (top risk).** Core must pin the identical arrow
  version **and crate form** as `-native` (both use the umbrella `arrow=55`); `ArrayRef`/
  `ArrayData` are concrete types crossing the crate boundary, so a version *or* resolution
  mismatch (e.g. core on split `arrow-array` vs native's umbrella re-export resolving to a
  different patch) is a type/linker error, not a graceful fallback. Any future arrow bump is a
  lockstep change to both crates.
- **Core build weight.** arrow-rs is heavier than `rustc-hash`; mitigate with
  `default-features = false` + the minimal subset. arrow-rs is pure Rust (no libclang) so
  Wave 0 still builds locally on Windows — no CI-only toolchain needed.
- **CI flip fragility.** A flaky native build could redden CI; the fallback lane stays
  independently green so a native-build hiccup never hides a real fallback regression.
- **Harness false confidence.** Weak fixtures = green harness hiding divergences; mitigate
  with reviewed adversarial generators per kernel, and Wave 0's empty-registry green run
  validating the harness mechanics against known-exact code.

## Testing

- Rust: `cargo test -p goldencheck-core` (kernels over Arrow arrays incl. null/empty/
  single-value cases); explicit `cargo build -p goldencheck-native` verification (grep
  `^error`, per the verify-Rust-builds-explicitly practice — piped-tail masks failures, and
  fmt is not clippy).
- Python: the parity harness both lanes; the full 550+ suite stays green under native-default.

## Sequencing within Wave 0 (TDD order)

1. Add arrow to core; convert **benford** first (simplest, integer histogram) end-to-end with
   null handling; update the native shim; build both crates; parity green — proves the
   pattern on one kernel.
2. Convert the other 4 (`fuzzy`, `composite_keys`, `functional_dependencies`,
   `approximate_fd`).
3. Promote `test_native_parity.py` → reusable `parity_harness.py` + empty accepted-divergence
   registry.
4. Loader flip (auto semantics, delete `_GATED_ON`, tuple-probe fix).
5. CI inversion (native-default lane + fallback lane).
6. Version bumps + `rollout-docs-sweep`.

## Non-goals (YAGNI)

- No new check kernels (Waves 1-6).
- No DuckDB/DataFusion/WASM SQL surface for goldencheck (Arrow-in-core merely *enables* a
  future one).
- No numpy/scipy work (Waves 4-5).
- No removal of pure-Python kernels — they remain the fallback.
