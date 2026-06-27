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
- `packages/typescript/goldenmatch/src/core/suggestColumnSignals.ts` — the TS
  `column_signals` builder (a first-class component; see "Caller-built column
  signals" below). Mirrors Python's `adapter.py::_build_column_signals_batch`.

## The kernel change (load-bearing)

`suggest-core` currently takes arrow `RecordBatch`es as a **mandatory** dependency
(`api.rs::suggest(scored_pairs, clusters, column_signals, …)`). The *rules*
(`rules.rs`) run on plain structs (`ScoreDiagnostics`, `ClusterDiagnostics`,
`&[ColumnSignal]`), but those diagnostic structs are **arrow-coupled today**: they
are built only via `ScoreDiagnostics::from_batch` / `ClusterDiagnostics::from_batch`
/ `column_signals_from_batch`, and `ColumnSignal` (`diagnostics.rs`) derives only
`Debug, Clone, PartialEq` — no serde. Arrow is the input encoding at the boundary;
WASM should not pull it (size + arrow-in-JS marshaling cost). The fix mirrors
`autoconfig-core`:

1. **Feature-gate arrow** in `suggest-core/Cargo.toml`: `arrow = { optional = true }`
   + `[features] arrow = ["dep:arrow"]`, default off. The arrow-typed
   `suggest(&RecordBatch, …)` and the `*::from_batch` constructors become
   `#[cfg(feature = "arrow")]`.
2. **Add serde derives + arrow-free constructors.** Add `Serialize, Deserialize` to
   `ColumnSignal`, and give `ScoreDiagnostics` / `ClusterDiagnostics` arrow-free
   constructors (e.g. `ScoreDiagnostics::from_scores(&[f64], cutoff)`,
   `ClusterDiagnostics::from_rows(...)`) that take the same raw values the arrow
   constructors extract. The arrow `*::from_batch` constructors are refactored to
   decode the batch and delegate to these — so both paths share the diagnostic math.
3. **Add a pure JSON entry point** (always compiled):
   `suggest_from_json(scored_pairs_json, clusters_json, column_signals_json,
   config_json, priors_json) -> Result<String, String>`. It deserializes the raw
   artifacts, builds the diagnostics via the arrow-free constructors, and runs the
   **same** `rules → rank` path, returning the same suggestion JSON.
4. The arrow `suggest()` stays as the `#[cfg(feature = "arrow")]` adapter and now
   shares the diagnostic + rule + rank code with the JSON path — one source of truth;
   its behavior is unchanged.
5. `native` (Python) keeps calling the arrow path (it has arrow batches in hand) —
   **zero Python-side change**. `suggest-wasm` calls the JSON path.

This is the only change to existing Rust. It is behavior-preserving (the existing
`suggest-core` golden tests must stay green) and is also what unblocks the future
C-ABI / DataFusion consumers.

### What the kernel computes vs. what the caller supplies

Two of the three input batches are **caller-built**, not kernel-derived — this is
the crux of the parity story:

- **Kernel-computed (single-sourced in Rust):** `ScoreDiagnostics` (histogram /
  dip / sub-cutoff mass) from `scored_pairs`, and `ClusterDiagnostics` (weak /
  oversized clusters) from `clusters`. The JSON path computes these in the kernel
  exactly as the arrow path does.
- **Caller-built (NOT computed by the kernel):** the `column_signals` rows. The
  kernel only *reads* `corruption_score`, `collision_rate`, `identity_score`,
  `variant_rate`, `cardinality_ratio`, `null_rate`, `col_type`, `in_blocking`. In
  Python these are produced by `adapter.py::_build_column_signals_batch` over a stack
  of Python modules (`compute_column_priors`, `_collision_rates`, the column
  classifier, `blocking_risk`, direct Polars). **TS must reproduce this** — see the
  next section. This is why end-to-end TS-run parity needs the column-signal port,
  while fixture-fed kernel parity does not.

## Caller-built column signals (TS)

`suggestColumnSignals.ts` builds the `column_signals` JSON the kernel reads, from a
TS run's rows + clusters + config. It mirrors `_build_column_signals_batch` field by
field, and the building blocks already exist in TS:

- `identity_score`, `corruption_score` → `indicators.ts::computeColumnPriors` (already
  documented as mirroring Python's `compute_column_priors`).
- `col_type` → `profiler.ts::profileColumn` (`ColumnProfile.colType`, the same
  classifier autoconfig uses).
- `cardinality_ratio`, `null_rate` → direct reduction over the rows.
- `in_blocking` → from the resolved config's blocking fields.
- `collision_rate` → a small TS reduction mirroring `_collision_rates` (fraction of
  multi-member clusters where the column's values disagree).
- `variant_rate` → `0.0` default (matches Python's behavior when goldencheck is
  absent; Python's `blocking_risk` source is optional there too).

Each field gets a **parity surface** (TS value == Python value on shared fixtures),
because this is where end-to-end drift would live — the kernel itself is already
covered by fixture-fed golden vectors.

## TS surface wiring (full default-pipeline parity)

Scoped to the surfaces TS actually has (no TS web/TUI/REST exist):

- **Core — `dedupe(rows, { suggest?, heal? })`** (`api.ts`): the result gains
  `suggestions` (serialized objects, same `{id, kind, target, rationale, verified,
  patch}` wire shape as Python) and `healTrail`.
  - **Default** (`suggest`/`heal` unset): a **free trigger** (`headroomSignal`)
    reads the run's `postflightReport` — no kernel call on a healthy result. Only on
    a trigger does the cheap artifacts-in path attach **raw, unverified** candidates
    from the run's `scoredPairs` / `clusters`.

    **Trigger signal — honest difference from Python.** Python's `headroom_signal`
    reads `controller_history.pick_committed().profile.health()` (RED/YELLOW) and
    `profile.scoring.dip_statistic`. The TS `PostflightReport`
    (`autoconfigVerify.ts`) carries only `signals` / `adjustments` / `advisories` —
    there is **no `controllerHistory`, health verdict, or `dip_statistic`** on the
    TS `DedupeResult`. So the TS trigger derives a **score-distribution** signal:
    fire when the postflight `scoreHistogram` is bimodal (sub-cutoff mass present) OR
    a threshold `adjustment` fired (postflight only adjusts in response to a clearly
    bimodal distribution). This is the dip half of Python's trigger, not the
    controller-health half — an accepted, documented divergence (threading
    controller health onto `DedupeResult` is out of scope). The trigger also handles
    `postflightReport === undefined` (the field is optional) → no fire.
  - **`suggest: true`** → the expensive verified path (each candidate simulated by a
    **TS pipeline re-run**, NOT a per-candidate WASM round-trip; the kernel is called
    once up front). Mirror Python's per-run candidate cap (`_MAX_VERIFY_CANDIDATES =
    8`). **`heal: true`** → the bounded apply-and-re-run loop, recording `healTrail`
    and returning the healed config.
- **CLI** (`cli.ts`): `--suggest` / `--heal` flags plus a free default-run hint
  (the hint reads the free trigger; no second pipeline run).
- **MCP** (`node/mcp/server.ts`): a `review_config` tool (mirror the Python MCP tool
  in this stack). The legacy `suggest_config` tool, if present, is untouched.
- **A2A** (`core/agent/skills.ts` + `node/a2a/server.ts`): a `review_config` skill.
  (Skills live in the edge-safe `core/agent/`, not `node/agent/`.)
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

- **Kernel golden-vector parity (success bar):** shared JSON fixtures in
  `tests/parity/fixtures/suggest/`. Given **identical input artifacts JSON** (the
  three batches + config + priors), the suggestion JSON is **byte-identical** across
  (a) the Python native path, (b) the `suggest-core` Rust golden test, and (c) the
  TS/WASM path. Fixtures are emitted by the build script from the Rust kernel so they
  cannot drift (the `autoconfig-wasm-*` parity tests are the precedent). This proves
  the kernel binding is correct; it does NOT by itself prove a TS `dedupe` run
  matches a Python one — that depends on the column-signal builder below.
- **Column-signal parity:** `suggestColumnSignals.ts` field values (identity,
  corruption, collision, cardinality, null, col_type, in_blocking, variant) ==
  Python's `_build_column_signals_batch` on shared fixtures. This is where
  end-to-end drift would live, so it gets its own parity surface (`computeColumnPriors`
  already has a TS parity test to extend).
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
