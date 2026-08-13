# 0059 — Semantic-layer structural certification is single-sourced through `key-integrity-core`

**Status:** accepted (2026-08-13, Ben) • **Ratifies (does not build):** the shipped `key-integrity-core` kernel + its Python-native / TS-wasm / SQL bindings • **Builds on:** [0047 (one product, two engines)](0047-one-product-two-engines-architecture.md), [0049 (metric-aware key certification)](0049-metric-aware-key-certification.md), [0046 (cross-language phase-handoff conformance)](0046-cross-language-phase-handoff-conformance.md) • **Planning:** [../planning/semantic-layer-interchange.md](../planning/semantic-layer-interchange.md)

## Context
An audit asked: "do the parity port to Rust kernels for the semantic layer, and the
other languages fall out from that." The finding was that **it is already done** — the
semantic layer's one clean columnar primitive (structural key-integrity certification:
uniqueness-at-grain + fan-out) is authored once in Rust and bound by every surface. The
gap was not code; it was that this single-sourcing was undocumented and kept being
re-discovered. This ADR ratifies the state and the one design choice inside it that
looks like a weakness but is not (the opt-in default), so it stops being re-litigated.

The capability: distinct key groups, `duplicate_key_groups`, `max_fan_out`,
`is_unique_at_grain`, and per-measure fan-out (`SUM(all) / SUM(per-group max)`).

## Decision (the ratified state)
1. **One authoritative owner: `key-integrity-core`.** The structural reduction lives in
   the pyo3/pgrx/wasm-free Rust crate `packages/rust/extensions/key-integrity-core`
   (`certify_structural` / the `certify_structural_json` JSON-in/JSON-out boundary). It
   is the single source of truth.
2. **Every non-Rust surface binds that one core** over the JSON boundary — chosen
   precisely because it is the shape wasm-bindgen, PyArrow, pgrx and DuckDB can all feed
   identically:
   - **TypeScript** ← `key-integrity-wasm` (a ~5-line wasm-bindgen re-export) →
     `certifyStructural`;
   - **Python** ← the `certify_structural_json` shim in `goldenmatch-native` →
     `_structural_native`;
   - **SQL** ← pgrx `goldenmatch_certify_structural` + the DuckDB UDF.
3. **The join-cardinality family sits on top with no second implementation.**
   `certify_serving_joins` / `certify_cube_joins` / `certify_osi_relationships` are thin
   wrappers that **delegate** to `certify_key_integrity` on **both** Python and TS, so
   cardinality trust inherits the one kernel rather than re-deriving group-by per surface.
4. **Conformance is one shared golden oracle.**
   `key-integrity-core/golden/key_integrity_golden.json` is **generated from the Python
   reference** (`scripts/emit_key_integrity_golden.py`) and asserted by all three
   surfaces — Rust `include_str!` (`golden.rs`), Python direct-read
   (`test_key_integrity_native_parity.py`, which also asserts `native == pyarrow`), and
   TS `toBeCloseTo` (`key-integrity-wasm.parity.test.ts`). A `fixture_drift` CI job
   regenerates the TS copy from the live core so it cannot drift.
5. **The default execution path is deliberately the pure-Arrow / pure-TS reference, and
   the shared kernel is opt-in** (`GOLDENMATCH_KEY_INTEGRITY_NATIVE` on Python;
   `enableKeyIntegrityWasm()` on TS). This is recorded as
   `default_routed: opt-in` in `parity/thesis_conformance.yaml`
   (`semantic-key-integrity-single-sourced-kernel`), and `enableKeyIntegrityWasm` is
   listed under `default_routing.ts_batteries.opt_in`.

## Why opt-in default is the correct call, not a latent second source
This is the one point that reads like a T1 violation and is not. The two-engines
decision tests actively say *keep it opt-in* here:
- **T5 (Arrow at bulk boundaries).** pyarrow's native `group_by` already **is** the
  Arrow-at-bulk boundary and is the reference the golden is generated from. The kernel
  path adds a JSON marshal on top of it.
- **T3 (kernelize on measurement).** The JSON-marshaled kernel path is **measurably
  slower** than the pyarrow `group_by` on this shape. Kernelizing on measurement means
  the faster pure-Arrow path stays the default; the kernel exists for the **single-owner
  guarantee (T1)**, enforced by the golden gate, not for speed.
- Making the kernel the default would be a measured regression with no correctness gain —
  the surfaces are already byte/numerically identical by the golden. So "shared owner,
  classified-fallback default" is `default_routed: opt-in`, not `default_routed: false`
  (the latent-second-source flag).

## Conformance to the two-engines frame (0047)
- **One authoritative owner** ✅ — `key-integrity-core`; no second source of truth across
  Python / TS / SQL, including the delegating cardinality wrappers.
- **Conformance defines correctness** ✅ — one Python-generated golden, asserted exact
  (integer fields) / within `1e-9` (floats) on all three surfaces; drift-guarded.
- **Arrow at bulk boundaries** ✅ — the *default* path is native pyarrow `group_by`; the
  kernel's JSON boundary is the smallest stable primitive every non-Rust caller shares.
- **Kernelize on measurement** ✅ — the opt-in default is a measurement call, not an
  omission; documented as such so it can't be "fixed" into a regression.

## Consequences / honest flags
- **Ratifying, not additive.** No new capability ships here — this records what exists so
  the single-sourcing is auditable (thesis_conformance entry) instead of rediscovered.
- **The opt-in default is load-bearing.** If a future change flips either host to the
  kernel by default, the correct move is to re-measure first and, if it is a regression,
  keep it opt-in — do not treat `default_routed: opt-in` as a bug to close.
- **`key-integrity-native` as a dedicated crate does not exist** (the shim lives in the
  shared `goldenmatch-native` binding); this is intentional and sufficient — the core
  header notes a dedicated native crate "can follow" if the wiring ever warrants it.
- **The rest of the semantic layer stays Python-authoritative by design.**
  `discover_semantic_model`, the discovery slices, the resolution tier, the LLM namer and
  warehouse introspection are orchestration / stateful capabilities, not clean columnar
  cores; kernelizing them would force stateful logic into a columnar kernel (the T4
  anti-pattern). They are correctly `python_only`, not a parity gap.

---
**Classification:** decision/accepted • **Last updated:** 2026-08-13
