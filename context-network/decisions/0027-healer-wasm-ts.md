# 0027 — Config-suggestion healer on the TS/WASM surface

**Status:** Accepted • **Shipped:** Phase F (CI + docs) of the healer-wasm-ts arc (Tasks 1–13)

## Context

The config-suggestion engine (the "healer") reads a finished run's diagnostics —
score distribution, cluster quality, per-column signals — and proposes config edits
(lower/raise a threshold, swap a scorer, add a negative-evidence field), optionally
verifying or applying them in a bounded heal loop. It shipped first on Python as the
pyo3-free `suggest-core` kernel wired into the default `dedupe_df({suggest, heal})`
pipeline. The TypeScript port (`goldenmatch` on npm) is held to cross-surface parity
("every capability on every surface"), and the healer was the last default-pipeline
capability absent from it.

The repo already had a proven `-core → -wasm → TS` pattern for the autoconfig kernel:
one pyo3-free Rust crate, a `wasm-bindgen` cdylib binding alongside the Python pyo3
shim, a build script that embeds the wasm + copies golden fixtures, a lean registry
backend (graceful-empty default) + a heavy opt-in subpath, and a CI drift guard.
Inventing a second pattern for the healer would have been a needless divergence.

## Decision

Bring the healer to TS by compiling the existing `suggest-core` kernel to WebAssembly,
mirroring the autoconfig precedent exactly rather than reimplementing rule logic in TS.

- **Kernel refactor, zero Python behavior change.** `suggest-core`'s `arrow`
  dependency is feature-gated and the arrow constructors gain always-compiled
  arrow-free twins; a new `suggest_from_json` entry runs the identical per-matchkey
  rule loop as the arrow `suggest()` via a shared `suggest_core` fn (true single source
  of truth). The Python native path is untouched.
- **Second binding.** `suggest-wasm` is a `wasm-bindgen` cdylib over `suggest_from_json`
  (no arrow). `build_suggest_wasm.mjs` builds it, strips the async-init path,
  base64-embeds the wasm under committed `src/core/_wasm/`, and copies the kernel golden
  vectors into the TS parity fixtures.
- **Opt-in, graceful-empty.** The always-on TS healer surface (`suggest.ts`, wired into
  `dedupe()`) reaches the kernel through a lean `import type`-only registry; the heavy
  WASM module is behind the opt-in subpath `goldenmatch/core/suggest-wasm`, registered by
  `enableSuggestWasm()` — the exact TS analog of `pip install goldenmatch[native]`. With
  no backend registered, every surface (core, CLI, MCP `review_config`, A2A
  `review_config`) returns `[]` / `undefined` and never throws.
- **One shared cross-surface contract.** The suggestion golden vectors are authored by a
  `suggest-core` BLESS test (the independent oracle); the build script only copies them.
  The TS parity test (committed wasm vs fixtures) and a Python native cross-surface test
  run the SAME fixtures, so a suggestion's `{input → expected}` is identical across
  Python, Rust, and TS.
- **CI (this phase).** A `suggest_wasm` path filter gates a "Rebuild + verify embedded
  suggest wasm" drift-guard step in the `typescript` lane (rebuild, `git diff` the kernel
  fixtures only — NOT the toolchain-variant wasm bytes — then run the parity test);
  `suggest-core` (`--features arrow`) + `suggest-wasm` (`cargo check`) run in the `rust`
  lane.

## Consequence

- The healer now runs on every TS surface at full default-pipeline parity: the free
  trigger rides along on `dedupe()` (no kernel call when it doesn't fire — the cost
  guarantee), `{ suggest }` verifies, `{ heal }` applies-and-re-runs, and
  `GOLDENMATCH_SUGGEST_ON_DEDUPE=0` is the kill-switch.
- The MCP tool count moves 44 → 45 (`review_config`); the count is asserted dynamically
  from `TOOLS.length`, not hardcoded.
- Default TS bundles stay lean (no inlined wasm) and edge-safe — only subpath importers
  who call `enableSuggestWasm()` pay the wasm bytes. Not opting in is a no-op, never a
  failure.
- The drift guard diffs the kernel `{input,expected}` fixtures only (not the wasm bytes,
  which vary by toolchain, and not the Python-emitted `column_signals_basic.json`). The
  build script copies the committed crate golden; it does NOT re-bless — so the diff
  catches "crate golden changed but not copied". Stale committed wasm is caught
  behaviorally by the parity test.
- `GOLDENMATCH_SUGGEST_ON_DEDUPE` + the TS/WASM usage are documented on
  `docs-site/goldenmatch/config-suggestions.mdx`, and (since the rebase onto a
  main carrying the Python healer's tuning section) the canonical
  `docs-site/goldenmatch/tuning.mdx` flag index now carries its
  `GOLDENMATCH_SUGGEST_ON_DEDUPE` row.
