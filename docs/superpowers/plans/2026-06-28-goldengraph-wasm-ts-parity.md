# GoldenGraph (KG engine) on WASM + TS — Implementation Plan

> REQUIRED: superpowers:executing-plans / subagent-driven-development. Checkbox steps.

**Goal:** Surface the GoldenGraph knowledge-graph engine (build / query / community / temporal-store) into TS/JS via the **already-built** `goldengraph-wasm` crate — the TS analog of `goldengraph-native` (Python). The second fold of the goldengraph family into JS, mirroring the just-shipped `goldenprofile` package (PR #1303) as its template.

**Why now / why this:** ADR 0028 named `goldengraph-wasm` as the next fold after goldenprofile. Same exact shape: a pyo3-free `goldengraph-core` kernel, full matrix already present (`-native` Python, `-cabi` C, `-wasm`), only the TS wiring missing. Bigger than goldenprofile (7 functions vs 1) but the pattern is fully proven.

**Dependency:** rides PR #1303 (graph-core's `arrow` is now an opt-in default-on feature). Branch off `main` AFTER #1303 merges, then cherry-pick the local correction commit `1cc8f59c` (arrow-rationale fix), OR branch off the goldenprofile branch and rebase.

## De-risking already done (empirical, this session)
- `goldengraph-wasm` is self-contained: `goldengraph-core` + `serde_json` + `wasm-bindgen` only. **Builds to wasm32 clean** (verified: 636 KB gated / 637 KB ungated).
- `goldengraph-core` pulls `graph-core` WITHOUT `default-features=false` → drags arrow. Gating drops it: **build-time + dep-hygiene win** (skip ~11 arrow crates; 2m01s → 7.9s), NOT size (DCE strips unused arrow anyway: ~1 KB delta) and NOT "won't link" (arrow compiles to wasm32 fine). [This corrects the goldenprofile wording; correction commit `1cc8f59c`.]
- Surface = **7 functions** (each `*_impl` host + wasm wrapper):
  `build_graph(mentions, edges, resolution)`, `neighborhood(graph, seeds, hops)`,
  `seeds_by_name(graph, name)`, `communities(graph)`, `store_append(snapshot, batch)`,
  `store_as_of(snapshot, valid_t, tx_t)`, `store_history(snapshot, id)`.
- **Composes goldenprofile:** `build_graph`'s `resolution_json` arg IS goldenprofile's `Resolution` output. The goldengraph TS package can `dependencies: { goldenprofile }` so a caller pipelines resolve → build_graph. (Decide: hard dep vs just-accept-the-JSON.)

## Reference template (MIRROR goldenprofile exactly)
| Concern | Mirror |
|---|---|
| build script | `packages/typescript/goldenprofile/scripts/build_goldenprofile_wasm.mjs` |
| backend registry (edge-safe) | `.../src/core/goldenprofileWasmBackend.ts` |
| heavy opt-in module | `.../src/core/goldenprofileWasm.ts` |
| public index + types | `.../src/index.ts` |
| fixture generator (host oracle) | `packages/rust/extensions/goldenprofile-wasm/examples/gen_parity_fixtures.rs` |
| parity test (canonical compare) | `.../tests/parity/goldenprofile-wasm.parity.test.ts` |
| unit test (refusing contract) | `.../tests/unit/goldenprofileWasmBackend.test.ts` |
| scaffold (package.json/tsconfig/tsup/vitest) | `packages/typescript/goldenprofile/*` |
| CI drift guard + filter | `ci.yml` `goldenprofile_wasm` block |
| publish wiring | `publish-goldenprofile-js.yml` |
| ADR | `context-network/decisions/0028-goldenprofile-wasm-ts.md` |

## Phases (mirror goldenprofile A–F)
- [ ] **A — kernel/gating.** `goldengraph-core` → `graph-core { default-features = false }`. Verify host tests + `cargo build --target wasm32` clean. (Already measured; just apply + commit.)
- [ ] **B — build pipeline.** `scripts/build_goldengraph_wasm.mjs` (mirror; strip async init, base64 bytes, 7 fn names in the `.d.ts`). Committed `src/core/_wasm/*`.
- [ ] **C — package.** New standalone `packages/typescript/goldengraph` (pnpm workspace auto-joins). Backend registry exposing all 7 ops; opt-in `goldengraph/wasm`; `index.ts` typed surface (Mention/Edge/Graph/Snapshot/Neighborhood/Community types from the serde model — READ `goldengraph-core/src/model.rs` + store types). Each query fn REFUSES (throws) when wasm not enabled. Decide goldenprofile dep.
- [ ] **D — parity.** Extend `gen_parity_fixtures.rs` pattern: curated cases per fn (build_graph from a resolution; neighborhood hops; communities; store append→as_of→history bitemporal). Canonicalize (graph node/edge order, community partition, store ordering — these are HashMap-seed nondeterministic, same as goldenprofile). TS parity + Python cross-parity (importorskip goldengraph_native). Unit refusing-contract test.
- [ ] **E — bench.** Scale bench: build_graph + neighborhood over N nodes; enablement posture (no pure-TS to beat).
- [ ] **F — CI + docs.** `goldengraph_wasm` path-filter + drift guard in the typescript lane; `publish-goldengraph-js.yml` (wired-unfired); add `goldengraph` to NPM_PACKAGES; new ADR 0029; updates log; CHANGELOG. Add to issue #1304's scope (goldengraph_native CI lane) OR file a sibling.

## Open decisions
1. **Package home:** standalone `goldengraph` (recommended, mirrors the crate's standalone-workspace design + goldenprofile precedent). vs folding into goldenprofile (no — distinct engine).
2. **goldenprofile dependency:** hard `dependencies: { goldenprofile }` (clean pipeline `resolveProfiles → buildGraph`) vs accept raw resolution JSON (looser, zero dep). Lean hard-dep for the composed DX.
3. **npm name:** `goldengraph`.
4. **Temporal-store surface:** `store_append/as_of/history` is a bitemporal snapshot API — confirm it's in-scope for v1 TS or defer to graph+query only.

## Definition of done
goldengraph-wasm consumed by an edge-safe TS package; 7 ops resolve through the kernel via opt-in wasm; cross-surface parity (partition/structure + value, canonical); CI drift guard + docs; publish wired-unfired. Enablement posture (parity + scale), not acceleration.
