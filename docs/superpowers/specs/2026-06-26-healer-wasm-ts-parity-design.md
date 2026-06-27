# Healer on WASM + TS — design

**Status:** Approved (brainstorming) • **Date:** 2026-06-26

## Goal

Bring the **healer** (the config-suggestion loop) to the TypeScript/JavaScript
surface by compiling the existing `suggest-core` Rust kernel to WebAssembly, then
wiring it into the TS `goldenmatch` package with **full default-pipeline parity** —
the same automatic-surface-on-`dedupe` experience just shipped in Python. One Rust
kernel, three bindings (pyo3 native, wasm, and the future C-ABI/DataFusion).

## Context

`suggest-core` (`packages/rust/extensions/suggest-core/`) is the pyo3-free kernel
that ingests a finished run's artifacts, applies the threshold / scorer-swap /
negative-evidence rules, and ranks suggestions. Today it has exactly one binding:
the Python `native` (pyo3) shim, reached via `goldenmatch.core.suggest`.

The repo already has a well-worn `-core → -wasm → TS` pattern: `autoconfig-core`
compiles to `autoconfig-wasm` (a `wasm-bindgen` cdylib), is built by
`scripts/build_autoconfig_wasm.mjs` into committed `_wasm/` glue + base64 bytes +
golden fixtures, and is consumed by `autoconfigWasmBackend.ts` so the TS surface
runs the **same** decision core as the Python wheel. This design applies that
pattern to the healer.

The TS dedupe pipeline (`runDedupePipeline` / the public `dedupe()` in `api.ts`)
already returns `{ clusters, scoredPairs, postflightReport }` — exactly the
artifacts the kernel needs, including the postflight report that drives the free
trigger.

### Decisions taken during brainstorming

1. **Scope: full default-pipeline parity** (not kernel-only). The healer wires into
   the TS dedupe pipeline with the same `suggest`/`heal` options, free trigger, and
   heal loop as Python, across every TS surface (core, CLI, MCP, A2A).
2. **No-WASM behavior: graceful-empty** (mirror Python's native-required behavior).
   WASM is the single source of truth; no hand-maintained parallel TS rule engine.
   No WASM → attach no suggestions, never throw. (TS autoconfig keeps a pure-TS
   fallback because it is the zero-config entry and must always work; the healer is
   advisory, so empty is safe and keeps one kernel.)

## Architecture & components

The healer becomes a second consumer of `suggest-core`:

```
suggest-core (Rust, pyo3-free)            ← the ONLY rule/rank/diagnostics logic
  ├─ native/      (pyo3)   → Python goldenmatch.core.suggest    [exists]
  └─ suggest-wasm (cdylib, wasm-bindgen) → TS goldenmatch         [NEW]
```

**New artifacts:**

- `packages/rust/extensions/suggest-wasm/` — thin `wasm-bindgen` wrapper crate.
  Mirrors `autoconfig-wasm`: standalone `[workspace]`, `crate-type = ["cdylib"]`,
  `wasm-bindgen`, `serde_json`, `goldenmatch-suggest-core` path dep with NO arrow
  feature; `[profile.release] opt-level = "s"`, `lto = true`; and
  `[package.metadata.wasm-pack.profile.release] wasm-opt = false` (hermetic CI, no
  binaryen network fetch).
- `packages/typescript/goldenmatch/scripts/build_suggest_wasm.mjs` — regen script
  (mirror of `build_autoconfig_wasm.mjs`). Builds the crate with `wasm-pack`,
  neutralizes the async `__wbg_init` / `import.meta.url` path, and emits committed
  outputs: `src/core/_wasm/suggestWasmBindings.js`, `suggestWasmBindings.d.ts`,
  `suggestWasmBytes.ts` (base64 of the `.wasm`, edge-safe, no `fs`), plus golden
  fixtures under `tests/parity/fixtures/suggest/`.
- `packages/typescript/goldenmatch/src/core/suggestWasmBackend.ts` — loads the wasm
  once (`initSync(bytes)`), exposes the kernel call, reports "unavailable" on load
  failure (drives graceful-empty).
- `packages/typescript/goldenmatch/src/core/suggest.ts` — the TS healer module:
  `reviewConfig()`, `suggestFromResult()`, `heal()`, `serializeSuggestions()`,
  `headroomSignal()`, `maybeSuggest()` (mirrors the Python `goldenmatch/core/suggest/`
  surface and the `_api.py` wiring helpers).

## The kernel change (load-bearing)

`suggest-core` currently takes arrow `RecordBatch`es as a **mandatory** dependency
(`api.rs::suggest(scored_pairs, clusters, column_signals, …)`). The rule logic,
however, runs on serde-derived diagnostic structs (`ScoreDiagnostics`,
`ClusterDiagnostics`, `ColumnSignal`, `ConfigSummary`, `AcceptancePriors`, →
`Suggestion`); arrow is only the input encoding at the boundary. WASM should not
pull arrow (size + arrow-in-JS marshaling cost). The fix mirrors `autoconfig-core`:

1. **Feature-gate arrow** in `suggest-core/Cargo.toml`: `arrow = ["dep:arrow"]`,
   default off. The arrow-typed `suggest(&RecordBatch, …)` becomes
   `#[cfg(feature = "arrow")]`.
2. **Add a pure JSON entry point** (always compiled):
   `suggest_from_json(scored_pairs_json, clusters_json, column_signals_json,
   config_json, priors_json) -> Result<String, String>`. It deserializes the raw
   artifacts and runs the **same** `diagnostics → rules → rank` path, returning the
   same suggestion JSON the arrow path returns.
3. The arrow `suggest()` becomes a thin adapter: arrow batches → the same internal
   structs → shared core path. **Both paths share the diagnostics + rule + rank
   code** — one source of truth; the arrow path's behavior is unchanged.
4. `native` (Python) keeps calling the arrow path (it has arrow batches in hand) —
   **zero Python-side change**. `suggest-wasm` calls the JSON path.

This is the only change to existing Rust. It is behavior-preserving (the existing
`suggest-core` golden tests must stay green) and is also what unblocks the future
C-ABI / DataFusion consumers.

### Diagnostics computation lives in the kernel

The JSON entry takes the raw-ish artifacts (per-pair scores, cluster memberships,
per-column signal rows) and computes diagnostics **inside the kernel**, exactly as
the arrow path does. TS does NOT compute diagnostics — it only marshals its run's
`scoredPairs` / `clusters` / column profiles into the input JSON. This keeps the
diagnostic computation (histogram/dip, cohesion edges, corruption) single-sourced
in Rust and is what makes byte-parity achievable.

## TS surface wiring (full default-pipeline parity)

Scoped to the surfaces TS actually has (no TS web/TUI/REST exist):

- **Core — `dedupe(rows, { suggest?, heal? })`** (`api.ts`): the result gains
  `suggestions` (serialized objects, same `{id, kind, target, rationale, verified,
  patch}` wire shape as Python) and `healTrail`.
  - **Default** (`suggest`/`heal` unset): a **free trigger** reads the run's
    `postflightReport` (health RED/YELLOW or a score dip), mirroring Python's
    `headroom_signal` — no kernel call on a healthy result. Only on a trigger does
    the cheap artifacts-in path attach **raw, unverified** candidates from the run's
    `scoredPairs` / `clusters`.
  - **`suggest: true`** → the expensive verified path (each candidate simulated
    through the gate). **`heal: true`** → the bounded apply-and-re-run loop,
    recording `healTrail` and returning the healed config.
- **CLI** (`cli.ts`): `--suggest` / `--heal` flags plus a free default-run hint
  (the hint reads the free trigger; no second pipeline run).
- **MCP** (`node/mcp/server.ts`): a `review_config` tool (mirror the Python MCP tool
  in this stack). The legacy `suggest_config` tool, if present, is untouched.
- **A2A** (`node/agent/skills.ts` + `node/a2a/server.ts`): a `review_config` skill.
- **Kill-switch**: `GOLDENMATCH_SUGGEST_ON_DEDUPE=0` (read from Node env) **plus** an
  explicit `dedupe` option so edge/browser callers with no env can opt out.

## Error handling & graceful-degrade

- WASM load failure (unsupported runtime, corrupt bytes) is caught once in
  `suggestWasmBackend.ts`; the backend reports "unavailable" and every surface
  returns empty (`suggestions: []`, `healTrail` undefined), never throwing. Matches
  Python's `SuggestionsNativeRequired`-degrades-to-`[]`.
- The kernel returns `Result<String, String>`; a kernel `Err` (malformed input
  JSON) is logged and treated as empty, not fatal — the healer is advisory and must
  never break a `dedupe` call.
- No parallel TS rule engine exists to drift (per the graceful-empty decision).

## Testing

- **Golden-vector parity (success bar):** shared JSON fixtures in
  `tests/parity/fixtures/suggest/`. The **same** input artifacts produce
  **byte-identical** suggestion JSON from (a) the Python native path, (b) the
  `suggest-core` Rust golden test, and (c) the TS/WASM path. Fixtures are emitted by
  the build script from the Rust kernel so they cannot drift from the source of
  truth (the `autoconfig-wasm-*` parity tests are the precedent).
- **TS unit tests:** the trigger gate (no-op when healthy, no kernel call),
  `suggest` / `heal` option behavior, serialize wire shape, and graceful-empty when
  the WASM backend is stubbed unavailable.
- **Rust:** existing `suggest-core` golden tests stay green (behavior-preserving
  refactor); add a `suggest_from_json` == arrow-`suggest` equivalence test gated on
  the arrow feature.
- **CI:** a `suggest-wasm` parity lane (mirror the `autoconfig-wasm` lane) that
  rebuilds the wasm and asserts the committed `_wasm/` bytes + golden fixtures are
  in sync, so a kernel change cannot silently skip the regen step.

## Out of scope

- The C-ABI / DataFusion bindings of `suggest-core` (the arrow-gate refactor
  unblocks them, but wiring them is separate work).
- A pure-TS rule fallback (explicitly rejected — graceful-empty instead).
- TS web/TUI/REST surfaces (they do not exist in the TS package).

## Implementation notes (branch / base)

The WASM+TS work depends on `suggest-core` (introduced on the kernel branch / PR
\#1267) but NOT on the Python default-pipeline changes (PR #1275). It can branch off
`main` once #1267 lands, or off the kernel branch if started sooner. The arrow-gate
refactor of `suggest-core` is the first task and is shared by all consumers.
