# GoldenCheck Arrow-native kernel + deep-profiling expansion

GoldenCheck went from **zero Rust** to an optional compiled runtime plus a wave
of new data-quality checks — the same Arrow-native theme as the rest of the
suite, and the GoldenCheck sibling of `goldenmatch-native` / `goldenflow-native`.
`goldencheck` stays a pure-Python wheel; `pip install goldencheck[native]` pulls
the compiled kernels, and everything falls back to pure Python when they're
absent. Behaviour is identical either way — native only changes wall-clock.

**Status:** SHIPPED (2026-06-07, PR #793, merged to main).
**Decision:** [../decisions/0007-goldencheck-goldenmatch-integration.md](../decisions/0007-goldencheck-goldenmatch-integration.md) (shared with the integration arc).
**Code-level notes:** `packages/python/goldencheck/CLAUDE.md` (goldencheck-native section),
`packages/rust/extensions/goldencheck-native/README.md`. **Docs-site:** `goldencheck/native.mdx`.

## Crates + loader (the goldenmatch-native pattern)
- `packages/rust/extensions/goldencheck-core/` — pyo3-free kernels (the
  `score-core` analogue): Benford, composite-key / FD mining, fuzzy clustering.
- `packages/rust/extensions/goldencheck-native/` — abi3 PyO3 shim (standalone
  workspace, pinned `arrow=55`), reads Arrow zero-copy via `PyArrowType<ArrayData>`.
- Loader `goldencheck/core/_native_loader.py`: discover order `goldencheck._native`
  (in-tree `scripts/build_goldencheck_native.py`) → `goldencheck_native._native`
  (wheel) → pure Python. `GOLDENCHECK_NATIVE=auto|0|1`. A component runs native
  only if it's in `_GATED_ON` **and** its symbol is present — an explicit
  capability probe, not a silent `AttributeError` fallback (the goldenmatch #688
  footgun).

## The discipline: parity-exact AND measured-to-win
Every kernel cleared **two gates** before being switched on under `auto`: a
parity test (byte-identical / integer-exact vs the pure-Python reference) **and**
a wall-clock win over the Polars/Python baseline (`benchmarks/deep_profile_benchmark.py`).

**The lesson that earned its keep:** the first composite-key kernel hashed a
`Box<[u64]>` tuple per row and ran at **0.4×** — 2.5× *slower* than Polars'
vectorized+multithreaded `n_unique`. The gate caught it. Fix: interned ids are
dense and key columns low-cardinality, so mixed-radix **pack each row-tuple into
one `u128`** → allocation-free `FxHashSet<u128>` → 1.7×. *Don't gate a kernel on
"it's Rust"; gate on the measured wall vs the Polars baseline, which is already
fast.* Features where Polars already wins (duplicate rows, referential integrity,
freshness) stay pure-Polars on purpose.

## Kernels (parity-exact)
| Kernel | Speedup | Note |
|---|---|---|
| Benford histogram | ~16× | byte-identical incl. exact powers-of-ten (correctly-rounded `1e{exp}` table, not `powi`, matching Python's bignum `10**exp`) |
| Composite-key discovery | 1.7× | won only after the u128 packing fix above |
| Strict FD discovery | 12.8× | interns once + reuses across pairs + early-exits on first violation |
| Fuzzy value clustering | 76× | trigram+prefix blocking + pairwise Levenshtein over a column's distinct values |
| Approximate-FD violations | 15.5× | surfaces the ROWS that break a near-FD (likely data-entry errors) |

## New detection capabilities (the "cover more" half)
- **Composite-key discovery** — minimal multi-column keys when no single column is unique.
- **Strict + approximate FDs** — exact `det → dep` (redundant/lookup columns) and
  near-FDs with the offending rows surfaced (`zip → city` holds 99.7%; the 0.3% are errors). FP-guarded by a minimum average group size.
- **Fuzzy value clustering** — inconsistent categorical encodings (`California`/`Californa`/`CALIFORNIA`).
- **Exact + near-duplicate rows** — whole-row dupes (exact, and normalized-equal).
- **Referential integrity** (`goldencheck refs` CLI) — cross-file FK validation: orphan rows, orphan rate, join cardinality.
- **Freshness / staleness** — future-dated values (always-on) + name-gated staleness.
- **`--deep` mode** — profile the full population instead of the 100K sample cap.

## Public bridge APIs (consumed by GoldenMatch — see the integration node)
- `goldencheck.cell_quality(df)` → sparse `{(row_index, column): weight}` per-cell
  quality (fuzzy non-canonical + future-dated signals).
- `goldencheck.functional_dependencies(df)` → `[FunctionalDependency{determinant, dependents, confidence}]`.

## Boundary
Whole-ROW fuzzy matching is deliberately **not** here — that's entity resolution
(GoldenMatch's job). GoldenCheck stays at the value/column level.

## CI
`goldencheck_native` lane (mirrors goldenmatch's `native`): clippy both crates,
build the in-tree `.so`, run the parity suite with the ext present + a
`GOLDENCHECK_NATIVE=1` required-mode run. Publish via `publish-goldencheck-native.yml`
on a `goldencheck-native-v*` tag.

---
**Classification:** architecture/shipped • **Last updated:** 2026-06-07
