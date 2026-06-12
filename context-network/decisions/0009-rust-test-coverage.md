# 0009 — Rust test coverage: make the tests real, route around the dead-ends, then measure

**Status:** accepted (2026-06-09, Ben) • **Shipped:** PRs #827 / #830 / #832 (+ #826 bloom tests) • **Architecture:** [../architecture/rust-test-coverage.md](../architecture/rust-test-coverage.md)

## Context
A coverage audit of `packages/rust/extensions/` found the Rust tree's CI claims
were partly fictional. Standalone-workspace crates (`graph-core`, `score-core`)
had unit tests that never ran (`cargo test --workspace` only builds the `bridge`
member). The `postgres` crate's `#[pg_test]`s never executed (`cargo pgrx test`
appears nowhere). The `bridge` — the largest Rust file and the JSON marshalling
boundary for the whole SQL surface — had 6 tests that all self-skipped in CI,
making it an effective 0% false-green. And nothing was measured, so "what's left"
was guesswork.

## Decision
1. **Close the structural gaps where the mechanism works; route around it where
   it doesn't.** Wire the standalone crates' `cargo test` into a real lane; add
   native unit tests behind an `extension-module` feature-gate so `cargo test
   --no-default-features` links. But **don't chase `cargo pgrx test`** — it needs
   pgrx schema-gen, which is structurally broken for this crate (the same reason
   the SQL is hand-maintained). Assert the pgrx SQL surface via the psql smoke
   against a real `CREATE EXTENSION` instead; it tests the shipped SQL, which is
   stronger than auto-generated test wrappers.
2. **A test that doesn't run is worse than no test — make it loud.** The bridge's
   `Err -> eprintln -> pass` skip was the anti-pattern the whole audit was about.
   Replace it with an env-gated require (`GOLDENMATCH_BRIDGE_REQUIRE_PY=1` in CI →
   hard fail; unset locally → skip) and install goldenmatch into the bridge's
   embedded interpreter so the tests actually exercise the path.
3. **Stage CI-only changes behind a de-risk gate.** The bridge/native/pgrx work
   can only be verified by pushing (local Windows can't link pgrx, and the
   embedded interpreter imports polars → WMI-hangs). Prove the embedded-CPython
   path on the 6 existing bridge tests BEFORE writing 26 more; the staging caught
   the debian-`typing_extensions` install conflict (→ use `setup-python`) and the
   marshalling-assertion shape (structural, not strict `serde_json::from_str` —
   goldenmatch's reports embed control chars).
4. **Measure, don't assume — but keep it honest.** Add a `cargo-llvm-cov` job as
   an informational baseline, not a hard gate. Read the numbers as cargo-test
   coverage: `native`'s 26% is correct-but-misleading (it's Python-parity-tested),
   so a low number is not automatically a gap.

## Consequence
- The standalone crates run in CI; native has 18 Rust unit tests; the pgrx graph/
  fingerprint surface is psql-asserted; the bridge went 6 silent no-ops → 42 real
  marshalling tests; coverage is measured per crate.
- The bridge CPython-in-CI install dance (`setup-python` + pinned `PYO3_PYTHON`/
  `LD_LIBRARY_PATH` + `REQUIRE_PY`) is the reusable pattern for any pyo3-embedding
  crate; `rust_coverage` already reuses it.
- The data says there is no compelling remaining Rust gap — a per-crate floor
  gate on the measured baseline is the natural (optional) next step.
- `cargo pgrx test` is documented as a structural dead-end so it isn't retried.

---
**Classification:** decision • **Last updated:** 2026-06-09
