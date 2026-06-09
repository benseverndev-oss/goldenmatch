# Rust Test Coverage (the extensions tree)

How the `packages/rust/extensions/` crates are tested in CI, what each lane
covers, and the measured per-crate baseline. This is the cross-cutting result of
the 2026-06-08/09 Rust-coverage arc (audit → close the structural gaps → measure).

**Status:** SHIPPED — graph-core/score-core CI wiring + native unit tests + the
pgrx SQL surface (#827); the bloom CLK kernel's own Rust tests (#826); the
bridge marshalling tests (#830, 6 silent no-ops → 42 real tests); and the
measured-coverage job (#832).

## The crates and how each is actually tested

The extensions workspace's `cargo test --workspace` is misleadingly named — the
workspace is `members = ["bridge"]`, so it tests ONE crate. Every other crate is
its own standalone `[workspace]` and needs an explicit lane. The real map:

| Crate | Rust unit tests | How it runs in CI | Lane |
|---|---|---|---|
| `bridge` (pyo3, embeds CPython) | 42 | `cargo test --workspace` + goldenmatch installed into the embedded interpreter | `rust` |
| `graph-core` / `score-core` | 7 / 5 | explicit `cargo test --manifest-path` (standalone workspaces) | `rust` |
| `fingerprint-core` | 3 | `cargo test` + 3-surface parity | `native` |
| `native` (pyo3 extension-module) | 18 | `cargo test --no-default-features` + Python parity suite | `native` |
| `goldenembed` (ort) | 11 | `cargo test --release` | `goldenembed` |
| `postgres` (pgrx) | 5 `#[pg_test]` (DEAD) | psql smoke vs real `CREATE EXTENSION` | `rust_pgrx` |
| `datafusion-udf` | 0 | Python FFI tests (`test_datafusion_ffi_udf.py`) | `python` |

## Three load-bearing facts (each was a trap)

1. **`cargo pgrx test` cannot run for `goldenmatch_pg`.** It depends on pgrx SQL
   schema generation (installs each `#[pg_test]` into a `tests` schema), which is
   broken for this crate — the exact reason its SQL is hand-maintained and it's
   excluded from the workspace. So the 5 `#[pg_test]`s are dead; the pgrx SQL
   surface is asserted via the `rust_pgrx` **psql smoke** against a real
   `CREATE EXTENSION` (stronger: it tests the shipped SQL, not auto-generated
   wrappers). See [0009](../decisions/0009-rust-test-coverage.md).

2. **The bridge tests were a silent false-green.** `bridge/api.rs` (the JSON
   marshalling boundary for the whole Postgres + Rust surface) had 6 tests that
   all self-skipped (`Err -> eprintln -> pass`) because the `rust` job never
   installed goldenmatch — so the largest Rust file was effectively untested.
   Fix: install goldenmatch into the bridge's embedded interpreter (`setup-python`
   clean interp + pinned `PYO3_PYTHON`/`LD_LIBRARY_PATH`) and gate the skip behind
   `GOLDENMATCH_BRIDGE_REQUIRE_PY=1` (CI → a Python failure HARD-fails; unset
   locally → skip, so `cargo test` still works without the package).

3. **`native`'s low measured coverage is a measurement artifact, not a gap.**
   native is a pyo3 boundary crate: its 18 Rust tests cover the pure helpers
   (soundex, featurizer, pairs-math, C-ABI), but the bulk of its lines live in
   `#[pyfunction]` wrappers + Arrow/rayon paths exercised by the **Python parity**
   suite, which `cargo-llvm-cov` can't see (it only instruments `cargo test`).

## Measured coverage (`rust_coverage` lane, baseline 2026-06-09)

`cargo-llvm-cov` per crate, posted to the job summary + grep-able
`COVERAGE_RESULT` log markers. Informational baseline (no floor gate yet) +
tolerant per-crate (a crate that won't instrument degrades to "(failed)").

| Crate | Line coverage |
|---|---:|
| `score-core` | 95.7% |
| `fingerprint-core` | 83.9% |
| `graph-core` | 76.4% |
| `bridge` | 74.7% |
| `goldenembed` | 71.8% |
| `native` | 25.8% (measurement artifact — Python-parity-tested) |

The well-tested crates are 76–96%; the bridge marshalling layer is 75% (uncovered
≈ error/edge paths); the one "low" crate is low only because its real coverage is
via Python parity. There is no compelling remaining Rust gap — the work is done,
now provable and regression-guardable.

## Entry points

- `.github/workflows/ci.yml`: jobs `rust`, `native`, `native_wheel`, `goldenembed`,
  `embed_wheel`, `rust_pgrx` (PG 15/16/17), and `rust_coverage`.
- The bridge CPython-in-CI install dance is the canonical pattern for any future
  pyo3-embedding crate that calls goldenmatch (reused by `rust_coverage`).

## Related

- [sql-native-extensions.md](sql-native-extensions.md) — the graph/embed UDF
  surfaces these crates expose (its Verification section is the #509-scoped view).
- [0009 — Rust test coverage strategy](../decisions/0009-rust-test-coverage.md).

---
**Classification:** architecture/active • **Last updated:** 2026-06-09
