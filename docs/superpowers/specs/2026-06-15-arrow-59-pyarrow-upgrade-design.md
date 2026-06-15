# Arrow 55 -> 59 Upgrade for the pyarrow Crates (Tiers 1+2)

**Date:** 2026-06-15
**Status:** Approved design, pre-implementation
**Author:** Ben Severn (with Claude)

## Problem

Dependabot #999 proposed bumping `graph-core` from arrow 55 to 59, but a
graph-core-only bump breaks the build: `native` path-depends on `graph-core` and
shares arrow `ArrayData` types across the `dedup_pairs_arrow_data` boundary, so
the `goldenmatch-native` maturin wheel fails to compile when the two are on
different arrow majors. That failure cascades to every `uv sync --all-packages`
lane. #999 is parked as a tracked reminder
([[project_arrow_59_workspace_upgrade]]).

The goal is to move the suite's **pyarrow-FFI crates** to arrow 59 (currency +
consistency), done in a way that respects the one real coupling and avoids the
disproportionate datafusion upgrade.

## Key facts established during investigation (current `main`, 08472db0)

- **Every Rust extension crate is its own standalone cargo workspace** (separate
  `Cargo.lock`, separate wheel). Arrow types never cross between wheels at
  runtime; the interchange is pyarrow/polars over Arrow's stable C ABI. So
  different crates can run different arrow majors with zero runtime conflict; the
  only constraint is *within a single build graph*.
- **arrow versions today:** `native`, `graph-core`, `analysis-native`,
  `goldencheck-native`, `native-flow` = **arrow 55** (with the `pyarrow`
  feature, except graph-core which is internal-only); `datafusion-udf` = **arrow
  58** with **datafusion 53**.
- **graph-core is consumed three ways, only one is arrow-coupled:**
  - `native -> graph-core` uses graph-core's **arrow kernels**
    (`dedup_pairs_arrow_data(ArrayData...) -> (ArrayData...)`), sharing arrow
    Rust types -> **must match arrow major.** This is what #999 broke.
  - `datafusion-udf -> graph-core` calls **only the arrow-free slice kernels**
    (`Vec<(i64,i64,f64)>` in / `Vec<Vec<i64>>` out). Per the `ARROW-VERSION` note
    in `datafusion-udf/src/graph_udf.rs`: "No arrow type crosses the 58<->55
    boundary, so the mismatch is irrelevant." Insulated.
  - `postgres -> graph-core` uses the same arrow-free path (pgrx uses PG arrays).
- `analysis-native`, `goldencheck-native`, `native-flow` do **not** depend on
  graph-core or each other -- their arrow 55 is independent.

## Scope

In scope: move the five pyarrow-FFI crates to arrow 59.
- **Tier 1:** `graph-core` + `native` (the coupled pair).
- **Tier 2:** `analysis-native`, `goldencheck-native`, `native-flow`
  (independent).

Out of scope: `datafusion-udf` stays on arrow 58 / datafusion 53. It is insulated
via the arrow-free graph-core boundary, so it needs nothing, and moving it would
require a datafusion 53 -> 5x major upgrade (heavy breaking changes in datafusion
+ the young `datafusion-ffi` surface) for zero functional payoff. Explicitly
declined.

## Approach: two phases

### Phase 1 -- the coupled pair (one PR)
`graph-core` + `native` -> arrow 59 **together** in a single PR. They must move
in lockstep because of the shared `ArrayData` boundary. This phase unblocks the
original #999 intent.

### Phase 2 -- the independents (three separate PRs)
`analysis-native`, `goldencheck-native`, `native-flow` -> arrow 59, each its own
PR. No ordering constraint (standalone workspaces, no shared deps); they may land
in any order or in parallel.

## Per-crate mechanics (same recipe each)

1. Bump `arrow = "55"` -> `"59"` in the crate's `Cargo.toml` (preserve
   `default-features = false` and the `pyarrow` / other feature flags).
2. Regenerate that crate's `Cargo.lock`.
3. Fix the 55 -> 59 API breakages in the Rust code. The arrow surface these
   crates touch is small (`ArrayData`, `Int64Array`/`Float64Array`/`StringArray`,
   `ListArray`, the array builders, `DataType`, the `pyarrow` + `ffi` features),
   so the breaking-change surface should be modest -- but the exact breaks are a
   known-unknown until the first build (see Risks).
4. Rebuild the wheel; run the crate's native-parity suite.

**Naming note:** the spec uses *directory* names throughout, but the published
wheel + parity suite follow the *package* name, which differs. Confirmed:
`native/` -> `goldenmatch-native`, `graph-core/` -> `goldenmatch-graph-core`,
`native-flow/` -> `goldenflow-native`. Check each crate's `[package].name`
before assuming a wheel/test name.

## Components (isolated, testable units)

| Unit | What changes | Proof it works |
|------|--------------|----------------|
| **Phase 1 PR** (graph-core + native) | both Cargo.toml arrow->59, both Cargo.lock, code fixes in both | `goldenmatch-native` wheel builds; graph-core arrow-kernel parity tests pass; the full-matrix `merge_group` run (the one #999 broke) goes green |
| **Phase 2 PR: analysis-native** | Cargo.toml/lock arrow->59 + code fixes | analysis-native wheel builds + its parity suite passes |
| **Phase 2 PR: goldencheck-native** | same | goldencheck-native wheel builds + parity passes |
| **Phase 2 PR: native-flow** | same | native-flow wheel builds + parity passes |

## Validation

- The **merge queue's full-matrix `merge_group` run is the real integration
  gate** -- it builds every wheel via `uv sync --all-packages`, exactly what
  caught #999. Each phase's PR must pass it.
- Each crate's existing **native-parity suite** (Python compares native-kernel
  output to pure-Python) must pass on arrow 59 -- this confirms behavior is
  unchanged, not just that it compiles.
- The `pyarrow`-feature ABI risk is low (stable C Data Interface); the wheel
  build + a parity round-trip confirms interop with the env's pyarrow.

## #999 disposition

Phase 1 supersedes #999 (graph-core-only, fundamentally wrong since it must be
paired with native). When Phase 1 lands: **close #999** and update the parked
reminder ([[project_arrow_59_workspace_upgrade]]) + its PR comment.

## Risks / unknowns

- **Breaking-change surface unknown until built.** Mitigation: the first
  implementation step per crate is a throwaway `cargo build` to enumerate the
  breaks, then fix iteratively. A plan step, not a design risk.
- **pyarrow ABI.** Expected fine (stable C Data Interface); the wheel build +
  parity round-trip proves it. If a specific pyarrow version is required by arrow
  59's `pyarrow` feature, pin/raise it in the build env.
- **datafusion-udf boundary.** Confirm it still calls only the arrow-free slice
  kernels (it does today; nothing in this work changes graph-core's slice-kernel
  signatures). No change needed, but the Phase 1 PR should not alter those
  signatures.

## Out-of-scope / follow-ups

- datafusion-udf -> arrow 59 / datafusion 5x (declined; revisit only if a
  datafusion feature is needed).
- String-id (`_str`) graph kernels for datafusion-udf (pre-existing follow-up
  noted in the ARROW-VERSION note; unrelated to this upgrade).
