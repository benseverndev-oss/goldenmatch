# 0029 — GoldenGraph (KG engine) on WASM + TS: standalone, opt-in, graph+query v1

**Status:** accepted (2026-06-28, Ben) • **Plan:** `docs/superpowers/plans/2026-06-28-goldengraph-wasm-ts-parity.md` • **Sibling precedent:** [0028-goldenprofile-wasm-ts.md](0028-goldenprofile-wasm-ts.md) • **WASM policy:** [0014-opt-in-wasm-acceleration.md](0014-opt-in-wasm-acceleration.md)

## Context
The GoldenGraph knowledge-graph engine (`goldengraph-core`) is the second fold
named in ADR 0028. Same shape as goldenprofile: a pyo3-free kernel with the full
matrix already present (`-native` Python, `-cabi` C, `-wasm`), only the TS wiring
missing. Bigger surface — 7 kernel ops — and it composes goldenprofile (its
`build_graph` consumes a resolution).

## Decision
1. **Standalone `goldengraph` npm package** (mirrors goldenprofile + the crate's
   standalone-workspace design). Pure-by-default base entry (zero wasm bytes,
   edge-safe) + an opt-in `goldengraph/wasm` subpath. Query functions REFUSE
   (throw) when the backend isn't enabled — never a fake result.
2. **v1 = the 4 graph+query ops** (`buildGraph` / `neighborhood` / `seedsByName`
   / `communities`). **The bitemporal store** (`store_append` / `store_as_of` /
   `store_history`) was deferred as a separable fast-follow. **UPDATE (0.2.0,
   2026-06-28): the store shipped** — `appendBatch` / `asOf` / `history` over a
   portable JSON `Snapshot`, with parity fixtures for the append→as_of→history
   flow. Gotcha: the kernel's i64/u64 params (`valid_t`/`tx_t`/`id`) map to
   wasm-bindgen **BigInt**; the public API takes `number` and converts at the
   wasm boundary.
3. **Zero runtime deps.** The resolution input is a `{mentionIndex: entityId}`
   map or `["native", scorerId, threshold]` — NOT goldenprofile's `Resolution`
   shape — so no hard goldenprofile dependency. Composition (`resolveProfiles →
   buildGraph`) is a future adapter, not a pass-through.
4. **Cross-surface contract = partition/structure + values, canonicalized** (the
   graph entity/edge ordering can fall out of hash-map order; `communities` is
   already deterministic). Idempotent fixtures gate the TS-wasm parity test.
5. **Enablement gate = parity + scale**, not acceleration (no pre-existing TS
   engine to beat).

## Enabling change
`goldengraph-core` → `graph-core { default-features = false }` (build-time +
dep-hygiene win; arrow compiles to wasm32 fine and DCE strips it — same accurate
rationale as [0028](0028-goldenprofile-wasm-ts.md), corrected here in lockstep).

## Consequences / honest flags
- **No Python cross-parity test** (unlike goldenprofile). `goldengraph_native`'s
  API is object-oriented (a graph object with methods), not the clean JSON
  boundary goldenprofile's `resolve_json` offered — and it wouldn't run in CI
  anyway (`goldengraph` isn't in the python matrix; see issue #1304). The host
  fixtures come from the same kernel the native wheel wraps, so structural parity
  is by construction; the live gate is the TS-wasm-vs-fixtures parity + drift guard.
- **Bitemporal store deferred** (see Decision 2). README + CHANGELOG flag it.
- **Publish wired but unfired** (`publish-goldengraph-js.yml`, tag `goldengraph-js-v*`).

## Alternatives not taken
- Include the store in v1 (declined — heaviest surface, separable; ship the core
  KG capability first).
- Hard `dependencies: { goldenprofile }` (declined — the resolution shapes differ;
  a converter, not a pass-through; keep v1 zero-dep).

---
**Classification:** decision/accepted • **Last updated:** 2026-06-28
