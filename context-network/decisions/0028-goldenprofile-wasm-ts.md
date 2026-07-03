# 0028 — GoldenProfile (Virtual Fingerprint) on WASM + TS: a standalone, opt-in, enablement surface

**Status:** accepted (2026-06-28, Ben) • **Plan:** `docs/superpowers/plans/2026-06-28-goldenprofile-wasm-ts-parity.md` • **Kernel:** [0023-semantic-signature-virtual-fingerprint-engine.md](0023-semantic-signature-virtual-fingerprint-engine.md) • **WASM policy:** [0014-opt-in-wasm-acceleration.md](0014-opt-in-wasm-acceleration.md) • **Sibling precedent:** [0027-healer-wasm-ts.md](0027-healer-wasm-ts.md)

## Context
The GoldenProfile Virtual Fingerprint engine (cross-document entity resolution,
ADR 0023) is a pyo3-free Rust kernel (`goldenprofile-core`) already surfaced into
Python (`goldenprofile-native`) and C (`goldenprofile-cabi`), and a
`goldenprofile-wasm` (`wasm-bindgen`) crate already existed. Every leg of the
"one kernel, many surfaces" matrix was present **except the TS wiring** — the
last unfinished surface for this engine. Its only consumer is the `goldengraph`
Python package, which has no TS package; so this is also the first beachhead of
goldengraph's world into JS.

## Decision
1. **Standalone `goldenprofile` npm package**, not folded into goldenmatch.
   The crate family is a deliberate standalone workspace; the resolver is a
   self-contained `resolve_json` boundary. `goldengraph-wasm` (22 fns) is the
   larger follow-on that mirrors this package's pattern.
2. **Follows the healer (suggest) precedent, NOT the score/analysis one.** There
   is no pre-existing pure-TS resolver, so the ADR-0014 "pure-TS default +
   measured-win-vs-pure-TS gate" does not apply. The kernel is reached only via
   the **opt-in** `goldenprofile/wasm` subpath; the base entry is pure types +
   the resolver + a zero-byte registry (edge-safe, zero wasm bytes by default).
3. **Refuse, don't fake.** `resolveProfiles()` throws an actionable error when
   the wasm backend isn't enabled — the exact analog of Python's `_engine()`
   raise. An empty Resolution would be silently wrong, so it is never returned.
4. **The cross-surface contract is the PARTITION + edge set + scores (4dp), NOT
   byte-ordering.** The kernel emits cluster/edge ordering that is
   nondeterministic run-to-run (Rust `HashMap` seed). Fixtures and both parity
   tests (TS-wasm + Python-native) are canonicalized; the canonical fixture is
   idempotent across release runs so the CI staleness guard stays stable. (This
   corrects the plan's initial "byte-identical clusters" wording.)
5. **Binding gate = parity + scale, the enablement track.** This is not an
   acceleration; there was no TS feature before. The gate is 4dp parity across
   surfaces + a scale bench (10k/50k profiles) proving a single boundary crossing
   and no large-array footgun — explicitly NOT a speedup-vs-pure-TS claim.

## Enabling change
`graph-core`'s `arrow` dependency became an **opt-in, default-on** feature.
`goldenprofile-core` uses only the arrow-free `connected_components`, so
`default-features = false` drops arrow from the wasm build. (Honest note: arrow
*does* compile to wasm32, and dead-code elimination already strips it from the
linked `.wasm` — measured ~1 KB difference — so this is a **build-time +
dep-hygiene** win, ~11 fewer arrow crates to compile, NOT a "won't link" or size
requirement as first assumed.) Default-on means native / pgrx / datafusion
compile byte-identically (verified: default build all tests pass;
`--no-default-features` clean). Mirrors the autoconfig-core arrow-gating precedent.

## Consequences / honest flags
- **The Python cross-parity test `importorskip`s `goldenprofile_native` and only
  executes where that wheel is built + `goldengraph` is in the test set.**
  `goldengraph` is not in the CI python matrix today, so that test skips in CI —
  the TS parity (committed wasm vs canonical fixtures) is the live gate. The
  Python side is the same kernel by construction; the cross-parity is
  belt-and-suspenders, to be activated when a goldenprofile-native lane lands.
- **Drift guard, not byte guard.** CI rebuilds the wasm + regenerates fixtures
  and diffs ONLY the fixtures (the `.wasm`/`.js` bytes vary with the CI
  toolchain). Stale committed wasm is caught behaviorally by the parity test.
- **Publish is wired but unfired** (`publish-goldenprofile-js.yml`, tag
  `goldenprofile-js-v*`) pending Ben's go on the public name.

## Alternatives not taken
- Fold into goldenmatch TS as another opt-in subpath (declined — category error;
  goldenprofile is goldengraph's engine, not goldenmatch's).
- Stand up a full `goldengraph` TS package now and land the resolver inside it
  (declined as the first step — bigger; standalone is the minimal beachhead, and
  goldengraph-TS becomes the follow-on that can depend on this).
- Port the resolver to pure TS as an edge-safe default (declined — large, and the
  healer precedent already establishes opt-in-or-absent without a pure-TS port).

---
**Classification:** decision/accepted • **Last updated:** 2026-06-28
