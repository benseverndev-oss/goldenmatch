# Rust-thesis package audit — the weakest package

**Status:** audit snapshot. **Owner:** ER platform. **Created:** 2026-07-06.

## The thesis being audited against

Two design docs — reinforced by essentially every recent Rust-cutover PR
(#1490/#1492/#1496 infermap, #1472/#1478 goldenanalysis, #1469 goldencheck SQL,
#1488/#1495 goldenflow, #1489 goldenpipe) — define a **two-pillar Rust thesis**:

1. **Rust is the reference** (`2026-07-01-rust-is-the-reference-roadmap.md`). The
   pyo3-free Rust `*-core` kernel is the **authoritative** implementation of each
   primitive; pure-Python is a non-authoritative, explicitly-lossy fallback for
   platforms without a wheel. Native runs by default (`auto` = wherever a kernel
   symbol exists), and the native lane is meant to be a **required** CI gate
   (Rust-as-oracle), not an `@native_only` skip.

2. **Cross-surface parity** (`2026-07-04-cross-surface-parity-roadmap.md`). Each
   `*-core` compiles to **every surface it makes sense for** — Python (native +
   fallback), edge TS/WASM, DuckDB, Postgres, warehouse (BigQuery/Snowflake) —
   byte-identical, one source of truth. "The bar is native, not bridged": a
   CPython-in-a-box UDF does **not** count.

A package is therefore strong on the thesis to the degree that (a) its real
compute lives in a shared `*-core`, (b) native is authoritative **and
gate-enforced**, and (c) that core reaches multiple surfaces.

## Scorecard (2026-07-06)

| Package | `-core` | native (Py) | WASM/edge | DuckDB | PG | native = **required** gate? | Surfaces |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| **goldenmatch** | ✅ (score/graph/fingerprint/sketch/hnsw/embed/perceptual/autoconfig/suggest) | ✅ | ✅ | ✅ | ✅ | ✅ (`native`, `native_wheel`, `rust_pgrx`…) | 5 |
| **goldencheck** | ✅ | ✅ | ✅ | ✅ (P5) | ✅ (P5) | ✅ (`goldencheck_native`) | 5 |
| **goldenflow** | ✅ | ✅ (`native-flow`) | ✅ | ✅ (v0.1.1) | 🟡 partial | ✅ (`native_flow`, `goldenflow_duckdb`) | 4 |
| **goldenanalysis** | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ advisory | 3 |
| **goldengraph** | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | 3 |
| **goldenpipe** | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ (Rust is a *parity oracle only*, by design) | ~2 |
| **infermap** | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ advisory | **1** |
| goldenmatch-kg | — | — | — | — | — | — | 0 (integration shim) |

`ci-required` membership verified in `.github/workflows/ci.yml:3802` — the
`needs:` list includes `native`, `goldencheck_native`, `native_flow`,
`goldenflow_duckdb`, `rust_pgrx`, `pgrx_sql_sync`, `fixture_drift`; it does **not**
include `infermap_native` or `analysis_native`.

## Weakest package: **infermap**

Among packages genuinely pursuing the thesis, infermap is weakest on **both**
pillars.

### Cross-surface (pillar 2) — worst of any core kernel package
Only `infermap-core` + `infermap-native` exist. There is **no `infermap-wasm`,
no DuckDB, no Postgres** surface — it reaches **1 of 5** surfaces (Python native
only). Every other real-kernel package reaches at least 3. It is also the newest
to the fold (Wave 1 was only #1490), so it starts furthest back.

### Authority (pillar 1) — not gate-enforced, and not byte-exact
- Its native lane (`infermap_native`) is **absent from `ci-required`** — advisory
  only. "Rust is the reference" is therefore **not enforced** for infermap the way
  it is for goldenmatch/goldencheck/goldenflow, whose native lanes block the merge
  queue.
- Its `CLAUDE.md` documents that `str.lower()`/`\s` **diverge from Rust at
  non-ASCII** — a conscious, documented crack in the "byte-identical, one source
  of truth" invariant that the strong packages do not carry.

### Depth — the muscle is still host Python
Native-wired today: `detect` + **4 of 8** scorers (`exact`, `fuzzy_name`,
`initialism`, `profile` — verified by native-symbol refs). Still host: `pattern_type`,
`alias`, `llm`, and critically the **entire back-half of the pipeline** —
`assignment.py` (scipy Hungarian) and `calibration.py`. The compute-heaviest,
tie-break-sensitive stages (M×N assignment, calibration) — exactly what the thesis
wants in Rust — remain unported, and `CLAUDE.md` flags them as later waves with
real parity risk (LAP tie-break, rapidfuzz).

## Caveats on the ranking

- **goldenmatch-kg** has *zero* Rust but is out of scope: a thin integration
  adapter that drops in goldenmatch itself (excluded from the uv workspace, not a
  published-suite core, no primitives of its own). Nothing to make native.
- **goldenpipe** is weak on the *authority* pillar **deliberately** — it is an
  orchestrator ("smart pipe, dumb kernels"); its pure-Python planner is the runtime
  and Rust is only a parity oracle. A documented, scoped exception, not neglect.
- **goldenanalysis** shares infermap's advisory-gate status but is two surfaces
  ahead (has `analysis-wasm` + cross-surface parity fixtures), so it ranks above
  infermap.

## Highest-leverage moves to close the gap

1. **Add `infermap-wasm`** (Camp-B template — mirror `goldenanalysis-wasm`): the
   pure name-scorers + `detect` are already pyo3-free in `infermap-core`, so an
   edge surface is the cheapest surface-count win.
2. **Promote `infermap_native` into `ci-required`** so the Rust reference is
   gate-enforced, not advisory. (goldenanalysis is the sibling case.)
3. **Cut the assignment/calibration muscle into `infermap-core`** — the actual hot
   loop — with a LAP tie-break parity corpus, resolving the documented deferral.
