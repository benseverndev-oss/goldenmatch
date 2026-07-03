# goldenmatch (TypeScript)

npm package `goldenmatch`. Parity port of the Python sibling at `packages/python/goldenmatch/`. **At `v1.0.0` — first stable release (2026-06-15).** The API is stable; breaking changes only at the next major. See the wave history below for the road here.

> **Versioning note:** `package.json` was briefly set to `2.0.0` by #463 (the "Phase 5 plugin port" milestone label) and reverted — see `docs/versioning-policy.md` for why 1.0.0 (a "this is stable" signal), NOT 2.0.0 (PyPI-alignment), was the right cut. npm and PyPI keep **independent semver** (not lockstep): PyPI is at 2.0.x; the npm line is its own. The `0.x → 1.0.0` jump is the one-time stability declaration after the AgentSession/A2A port closed the last undeclared parity gap.

## Shared autoconfig wasm core (`goldenmatch/core/autoconfig-wasm`)
The autoconfig **planner (Layer 1)** + **classifier (Layer 2)** decision logic is a
single Rust crate (`packages/rust/extensions/autoconfig-core`) compiled to wasm and
shared by both the Python `goldenmatch-native` wheel and this TS package — one source
of truth, no hand-maintained parallel logic. The TS loader is `src/core/autoconfigWasm.ts`,
exposed as the **opt-in subpath** `goldenmatch/core/autoconfig-wasm`.
- **Distinct from the score-wasm runtime pattern below.** score-wasm uses the async
  `goldenmatch-wasm-runtime` (artifact built in CI, never committed, lazy dynamic import).
  The autoconfig loader is **synchronous** (`initSync` over an *inlined* base64 wasm) and
  edge-safe (no `node:*`) because the planner/classifier API must be sync. The wasm IS
  committed under `src/core/_wasm/` so `tsc`/`vitest`/`tsup` need no rust toolchain.
- **Regenerate the embed** (after any `autoconfig-core`/`autoconfig-wasm` change):
  `node scripts/build_autoconfig_wasm.mjs` (needs wasm-pack + the wasm32 target). It
  rebuilds the wasm, strips the wasm-bindgen async init path (which would drag
  `import.meta.url`/`fetch` into the CJS build), base64-embeds it, and copies the golden
  vectors into `tests/parity/fixtures/autoconfig/`.
- **Cross-surface parity gate:** `tests/parity/autoconfig-core.parity.test.ts` runs the
  same golden vectors as Rust (`tests/golden.rs`) + Python — 92 tests, byte-identical.
- **Opt-in backend, pure-TS default + fallback (the E3 posture, mirrors Python's default-OFF
  native gate).** The always-on planner/classifier reach the wasm through a tiny LEAN
  registry `src/core/autoconfigWasmBackend.ts` (`get/setAutoconfigWasmBackend`,
  `isAutoconfigWasmEnabled`) that `import type`s from the heavy loader (erased — zero bundle
  cost). Importing the heavy `goldenmatch/core/autoconfig-wasm` subpath and calling
  **`enableAutoconfigWasm()`** registers the backend; until then `applyPlannerRules` runs the
  pure-TS rules. **Why opt-in, not a hard reroute:** statically importing the loader into the
  main `core` graph bloats `core/index` 734KB → 2.4MB (the inlined wasm). The registry keeps
  default bundles lean (no wasm); only subpath importers pay the ~1.7MB. `disableAutoconfigWasm()`
  reverts (test isolation).
- **Planner reroute DONE (E3, planner half).** `autoconfigPlanner.ts::applyPlannerRules`
  prefers the registered wasm backend, else the TS rule table (kept as the faithful-port
  fallback — NOT deleted). Equivalence proven in
  `tests/parity/autoconfig-wasm-planner-equivalence.test.ts` (wasm plan ≡ pure-TS plan on
  every Python fixture ⇒ TS rules ≡ wasm ≡ Python).
- **Classifier reroute DONE (E3, classifier half).** `profiler.ts::profileColumn` routes
  through `backend.classifyColumns` when the wasm backend is enabled, else the hand-written
  heuristic (kept as the fallback). Unlike the planner, wasm ≠ pure-TS here (different
  classifiers), so there's no equivalence test — the core's correctness is the golden-vector
  parity; `tests/parity/autoconfig-wasm-classifier.test.ts` guards the wiring (email survives
  1:1, `identifier` flows through verbatim, no value outside `ColumnType` leaks, disable
  reverts). `profiler.ts` `import type`s the loader (erased), value-imports only the lean
  registry, so `core/index` stays lean (no wasm).
- **Classifier-vocab lever DONE (E3 follow-up).** `profiler.ts`'s `ColumnType` is now the
  core's FULL 13-value vocabulary (`email | name | phone | zip | address | geo | identifier |
  description | numeric | date | string | year | multi_name`) — no `coreColTypeToTs` remap;
  the wasm path assigns `inferredType = profile.colType` directly. The pure-TS heuristic
  (`guessTypeByName`/`guessTypeAndConfidence`) gained `address`/`description` name patterns and
  renamed `id`→`identifier`, `text`→`string` to match. Consumers updated for the new vocab:
  `autoconfig.ts::classifyColumn` reads `=== "identifier"` and surfaces `address` to its own
  `token_sort @ 0.8` kind (vs the free-text `@ 0.5`); `node/a2a|mcp/server.ts` fuzzy-suggest
  matches `string`/`address`/`description` (was `text`). NOTE: `complexityProfile.ts` has a
  SEPARATE coarser `ColumnType` (`text`/`id-like`/`unknown`, populated from JS `typeof`, used
  by `autoconfigRules.ts`) — that one is a different layer, untouched by this lever.

## Suggest-wasm healer core (`goldenmatch/core/suggest-wasm`)
The **config-suggestion engine** (the "healer") is the pyo3-free `suggest-core` crate
(`packages/rust/extensions/suggest-core`) compiled to wasm and shared by both the Python
`goldenmatch-native` wheel and this TS package — one kernel, no parallel JS rule logic.
It mirrors the autoconfig wasm pattern above exactly:
- **Synchronous `initSync` over an inlined base64 wasm**, committed under `src/core/_wasm/`
  (`suggestWasmBindings.{js,d.ts}` + `suggestWasmBytes.ts`) so `tsc`/`vitest`/`tsup` need no
  rust toolchain. Loader = `src/core/suggestWasm.ts`, exposed as the **opt-in subpath**
  `goldenmatch/core/suggest-wasm`.
- **Lean registry, graceful-empty default.** The always-on healer surface (`src/core/suggest.ts`:
  `serializeSuggestions` / `headroomSignal` / `maybeSuggest` / `reviewConfig` / `heal`, wired into
  `dedupe()` in `api.ts`) reaches the kernel through `src/core/suggestWasmBackend.ts`
  (`get/setSuggestWasmBackend`, `import type` only — erased). Importing the heavy subpath and
  calling **`enableSuggestWasm()`** registers the backend; until then every surface returns `[]` /
  `undefined` and NEVER throws (the `[native]`-analog posture — opt-in, pure graceful-empty
  default). `disableSuggestWasm()` reverts (test isolation).
- **Regenerate the embed** (after any `suggest-core`/`suggest-wasm` change):
  `node scripts/build_suggest_wasm.mjs` (needs wasm-pack + the wasm32 target). It rebuilds the
  wasm, strips the async-init path, base64-embeds it, and COPIES the kernel golden vectors from
  `suggest-core/tests/golden/suggest/` into `tests/parity/fixtures/suggest/`. The fixtures are
  AUTHORED by the suggest-core BLESS golden test (`BLESS_SUGGEST_FIXTURES=1 cargo test -p
  goldenmatch-suggest-core --features arrow --test bless`), the independent oracle — the build
  script only copies, it does NOT re-bless. (`column_signals_basic.json` is Python-emitted, not a
  kernel golden — the build script never touches it.)
- **Caller-built column signals.** The `column_signals` batch is built TS-side by
  `src/core/suggestColumnSignals.ts` (`buildColumnSignals`) over the existing profiler / indicator
  functions, with a Python-parity fixture.
- **Cross-surface parity gate:** `tests/parity/suggest-wasm.parity.test.ts` (committed wasm vs the
  copied golden fixtures) + the Python native cross-surface check on the SAME fixtures. CI: the
  `suggest_wasm` path filter gates a "Rebuild + verify embedded suggest wasm" drift-guard step in
  the `typescript` lane (rebuilds, `git diff --exit-code` the kernel fixtures only — NOT the
  toolchain-variant wasm bytes — then runs the parity test); `suggest-core` (`--features arrow`) +
  `suggest-wasm` (`cargo check`) run in the `rust` lane.

## Wave history
| npm | Python parity | Headline |
|-----|---------------|----------|
| 0.4.0 | v1.6.0 | Learning Memory + scorer ground truth |
| 0.5.0 | v1.7 + v1.8 | AutoConfigController, ComplexityProfile, RunHistory, StopReason telemetry |
| 0.6.0 | v1.9 + v1.10 | 5 complexity indicators + indicator-aware refit rules; scorer selection aligned with Python |
| 0.7.0 | v1.11 + v1.12 | NegativeEvidenceField + Path Y (exact-MK post-filter) |
| 0.8.0 | v1.15 (partial) | Identity Graph edge-safe core (`InMemoryIdentityStore` + query helpers). Persistent SQLite backend + pipeline-driven population deferred to a future wave. |
| 0.9.0 | v1.15 + persistent IdentityStore | `SqliteIdentityStore` in `src/node/identity/` — full IdentityStore interface (19 methods), schema byte-identical with Python so a `.goldenmatch/identity.db` is cross-toolkit readable. Pipeline-driven population + MCP identity tools deferred to v0.10. |
| 0.10.0 | v1.15 CLI + REST surface | `goldenmatch identity {list,show,history,conflicts,merge,split}` CLI subcommands + matching `/identities/*` REST routes (bound via `setServerIdentityStore`). Web UI / TUI / MCP-identity-tools / pipeline-driven `resolveClusters` still deferred. |
| 0.11.0 | core-algo catch-up + Phase 5 | Continuous-EM probabilistic (`trainEMContinuous`/`scoreProbabilisticContinuous`), `embedding`/`record_embedding` scorers (pluggable embedder shim — structural parity, not numeric), software/biblio domain extractors, autoconfig v3 planner + 3 tuners + `AutoConfigMemory`, and the golden-strategy plugin port (#208). Parity harness broadened with Python-generated goldens for blocker / clustering / golden-survivorship / discrete-EM. Still gappy: embedding numeric values (no torch/Vertex), cluster-threshold tuner is logic-parity only, `DOMAIN_EXTRACTED_COLS` still 3 vs Python's 12. |
| 0.14.0 (pending) | agent surface (AgentSession + A2A) | **AgentSession/A2A port (2026-06-15, the last undeclared parity gap).** Edge-safe `AgentSession` decision core + the shared `AGENT_SKILLS` registry + `dispatchSkill` (`src/core/agent/`); 14 agent-level MCP tools (MCP 30→44); A2A skill-union agent card + fail-closed bearer auth (`GOLDENMATCH_AGENT_TOKEN`) + unified `dispatchAnySkill` + `/tasks/send` + `/tasks/{id}/cancel`; node file-loaders (`analyzeFile`/`deduplicateFile`/`matchSourcesFile`). Behavior-fixture parity vs Python (`selectStrategy` decision table is the keystone). 4 waves, PRs #989/#994/#995 + this one. |
| 0.x (pending) | config-suggestion healer (WASM) | **Healer on TS/WASM (2026-06-27).** The `suggest-core` kernel compiled to wasm (`suggest-wasm`) wired into `dedupe()` at full default-pipeline parity: free trigger + `{ suggest }` verify + `{ heal }` loop, opt-in `enableSuggestWasm()` (the `[native]` analog) with graceful-empty default, on every surface (core / CLI / MCP `review_config` → MCP 44→45 / A2A `review_config` skill). Cross-surface golden-vector parity (TS == Rust == Python). See `context-network/decisions/0027-healer-wasm-ts.md`. |

Each wave's spec/plan: `docs/superpowers/specs/2026-05-10-ts-parity-arc-design.md`, `docs/superpowers/specs/2026-06-15-ts-agentsession-a2a-port-design.md` + per-wave plans.

## Deliberately not ported (Python deltas)
- **Python v1.13 (typed accessors).** TS strict mode (`noUncheckedIndexedAccess` + `exactOptionalPropertyTypes`) already enforces the same invariants at compile time.
- **Python v1.14 (controller surface-parity arc).** Threaded telemetry through TUI / CLI / Postgres / DuckDB surfaces that TS doesn't expose. TS already surfaces telemetry on its MCP server via the same `serialize_telemetry` JSON shape.
- **Python v1.16 (`backend="bucket"` 5M-on-one-node).** Polars-only Python path. TS runs edge-safe in Web Crypto and doesn't ship Polars — no TS analogue planned.

### Python-only by design (cross-surface parity audit, Wave 4 — declared, not a gap)
The 2026-05 parity audit flagged two large goldenmatch subsystems absent from the
TS port. After review these are **intentionally Python-only** — the cleaner close
than a multi-month port, mirroring the SQL "deferred-by-design" boundary in
`packages/rust/extensions/CLAUDE.md`:

- **Distributed engine / Ray / GPU** — the entire `goldenmatch/distributed/`
  (Ray-based loader, controller, clustering, golden, identity), `backends/ray_backend.py`,
  `core/gpu.py`, and `core/vertex_embedder.py`. No JS-ecosystem equivalent for Ray,
  Vertex embeddings, or GPU; the whole stack also assumes Polars. The TS package is
  edge-safe (Web Crypto, no Polars/Ray) and targets single-node / library / Workers
  use. **No TS port planned.** Users needing distributed ER use the Python package.
- **REST API + React web UI** — `goldenmatch/web/` (20 FastAPI routers + a React/Vite
  SPA) and the standalone `api/server.py`. The web UI is a full single-page app whose
  natural home is the Python package. TS already ships a thin programmatic
  `node/api/server.ts` for embedding in a Node service; the full browser UI is **not**
  ported and not planned. Run `goldenmatch serve-ui` (Python) for the UI.
- **Agent tools `sensitivity` / `incremental` / `certify_recall`** (2026-06-15) — the
  three agent-level tools whose Python implementations (`run_sensitivity` /
  `run_incremental` / `certify_recall`) have no TS core. The other **14** agent tools
  ARE ported (see the 0.14.0 wave row). Porting these three is a separate effort; for
  now they are Python-only and **NOT advertised** on the TS MCP/A2A surface (no silent
  gap — the card and tool list expose only what is wired).

Everything else (core scoring/blocking/clustering/golden, auto-config controller,
identity graph, PPRL, memory, MCP/A2A **incl. the AgentSession agent surface
(2026-06-15)**, CLI, connectors) IS ported — see the wave history above. This closes
the cross-surface parity roadmap.

## Commands
```bash
cd packages/typescript/goldenmatch
pnpm --filter goldenmatch test      # vitest (1266 tests at v0.11.0)
pnpm --filter goldenmatch typecheck # tsc --noEmit (strict)
pnpm --filter goldenmatch build     # tsup (5 entry points)
npx vitest run tests/parity/        # parity-only suite
```

## Edge-safety rule
`src/core/**` MUST NOT import `node:*`. Node-only code lives in `src/node/`. Memory backed by SQLite is `src/node/memory/`; the edge-safe interface is `src/core/memory/`. This is enforced by build separation, not by lint — verify when adding new imports.

## Strict TS
`noUncheckedIndexedAccess` + `exactOptionalPropertyTypes`. Idioms:
- Bounded-loop indices: use `arr[i]!` after a length check.
- Optional props: `...(x !== undefined ? { field: x } : {})` — never spread `undefined`.
- Optional peer deps (sqlite, sentence-transformers): `await import("pkg-name" as string)` — the `as string` cast prevents tsup from resolving at build time.

## Opt-in WASM scorer (score-wasm)
- `await enableWasm()` swaps a WASM backend (the Rust `score-core` crate compiled
  via `packages/rust/extensions/score-wasm/`) behind the sync `scoreMatrix` for
  COVERED scorers only: `jaro_winkler` / `levenshtein` / `token_sort` / `exact`.
  `token_sort` routes through score-core's `token_sort_normalized_ratio` (the
  TS-parity lowercase+strip normalize), NOT the un-normalized `score_one(2)` (the
  FFI/native asymmetry). Everything else stays pure-TS even when enabled.
  `disableWasm()` resets (test isolation, mirrors `setSyncEmbedder(null)`).
- Pure-TS is the default + fallback. `enableWasm()` returns `false` (pure-TS stays
  active) on any load failure; `{ require: true }` throws instead. Default users
  load zero wasm bytes (the loader/glue/bytes are behind a lazy dynamic import).
- Swap is at the NxN matrix boundary (one JS↔WASM crossing per block), never
  per-pair (boundary cost would dwarf a single scorer).
- The `.wasm` is NOT committed. Build locally: `bash packages/rust/extensions/
  score-wasm/build_wasm.sh` (needs the rustup `wasm32-unknown-unknown` target +
  `wasm-bindgen-cli`; the script installs the cli at the Cargo.lock-pinned
  wasm-bindgen version), then `npm run build`. CI's `wasm_score` lane builds it
  and runs `tests/parity/wasm-scorer.test.ts` un-skipped; without the artifact
  that test SKIPS and the artifact-free `wasm-backend`/`wasm-fallback` unit tests
  run in the normal `typescript` lane.
- **The parity gate pins WASM to canonical rapidfuzz `score_one` goldens.**
  `wasm-scorer.test.ts` asserts the WASM path reproduces the rapidfuzz values to
  4dp (verify/extend via a throwaway `score-core` test that prints `score_one`).
  **As of #879 the pure-TS scorers were ALIGNED with rapidfuzz** (codepoint
  iteration, the Winkler `>0.7` boost threshold, floored transposition `t//2`), so
  WASM ≈ pure-TS now holds too — the three prior known divergences are gone (e.g.
  `"saturday"/"sunday"` pure-TS 0.7475 → 0.7775). The goldens stay rapidfuzz-sourced
  because the WASM kernel IS rapidfuzz.
- **Dist artifact path (resolved):** the loader resolves the artifact via
  `new URL('./artifacts/score_wasm_bg.wasm', import.meta.url)`, which in the
  BUNDLED `dist` points at whichever location tsup lands the loader code. Rather
  than predict that, `copy_wasm_artifact.mjs` copies the artifact to EVERY
  plausible `./artifacts/` parent (`dist/core/wasm/artifacts/`, `dist/core/artifacts/`,
  `dist/artifacts/`). The `wasm_score` bench step is now a GATE (no longer
  `continue-on-error`): it builds dist + runs `enableWasm()`, so a broken bundled
  path reddens the lane (the bench `process.exit(1)`s on a failed enableWasm).
  Same pattern in the `analysis_wasm` lane / goldenanalysis.

## Universal WASM loader + cross-JS-target harnesses (R1 Workstream A)
- **`enableWasm({ universal: true })`** is an opt-in seam (alongside the default
  URL/fs/fetch loader) that resolves the artifact from a base64-INLINED module
  (`artifacts/score_wasm_base64.js`, emitted by `build_wasm.sh`, gitignored like
  the `.wasm`). No fetch/fs/`import.meta.url` asset resolution — the only path
  edge-safe across Workers + Deno + every bundler. Cost: base64 ~+33% over the raw
  `.wasm`. Decode lives in `goldenmatch-wasm-runtime` (`decodeWasmBase64`, `atob`
  or Buffer). DEFAULT path unchanged; default users load zero wasm bytes. Decision
  note: `docs/superpowers/notes/2026-06-14-wasm-universal-loader.md`.
- **Cross-target equivalence harnesses** (`tests/spike/`): the spike's pure-TS-vs-
  kernel 4dp assertion factored into runtime-agnostic `kernel-equivalence-core.ts`
  + frozen `fixtures/pure-ts-reference.json`. Per target: `kernel-equivalence.test.ts`
  (node, default vitest), `deno-kernel-equivalence.ts` (`deno test --no-check`),
  `browser-kernel-equivalence.test.ts` (`vitest.browser.config.ts`, Playwright
  chromium), `workers-kernel-equivalence.test.ts` (`vitest.workers.config.ts`,
  workerd). The browser + workers tests are EXCLUDED from the default `vitest run`
  (they need their own pools/globals) — run them via their `--config` files. CI:
  `.github/workflows/r1-kernel-js-targets.yml` (workflow_dispatch only).
- **Workers caveat (real constraint):** workerd BANS runtime WASM codegen
  (`WebAssembly.instantiate`/`new WebAssembly.Module` from bytes both throw "Wasm
  code generation disallowed by embedder"). So Workers does NOT use the base64-bytes
  universal path — it needs a BUILD-TIME CompiledWasm `.wasm` module import (the
  vitest-pool-workers convention; `modulesRules: [{ type: "CompiledWasm", include:
  ["**/*.wasm"] }]`). A Workers consumer of `enableWasm` must likewise ship the
  kernel as a build-time module, not via `{ universal: true }`.
- **Workers harness host-config gotcha:** the `workers-kernel-equivalence.test.ts`
  static `.wasm` import is parsed by the HOST vite `import-analysis` before the pool
  resolves it. Import the artifact as a **plain `.wasm`** (NOT `.wasm?module` — that
  suffix makes host import-analysis try to parse the bytes as JS, collecting 0 tests)
  and list `assetsInclude: ["**/*.wasm"]` in `vitest.workers.config.ts` so the host
  treats it as an opaque asset. `server.deps.inline` does NOT help — it only covers
  node_modules, never a local `src/**/*.wasm`.

## Parity contract
- **Scorer output:** 4-decimal tolerance vs Python (`tests/parity/scorer-ground-truth.test.ts`).
- **Hash bytes:** SHA-256 truncated to 16 hex via Web Crypto. UTF-8 mandatory. Hash input = values joined by `|` (NOT `<col>=<val>`). `__row_id__` excluded from `record_hash` so corrections survive row reordering.
- **PPRL parity** (Wave 4, 2026-06-05): `src/core/pprl/protocol.ts` is now a faithful port of `pprl/protocol.py` (replaced the "API-parity stub" that scored string-dice over hex chars). CLKs use the parameterized `bloom_filter:<ngram>:<k>:<size>[:hmac]` transform (the pure-TS SHA-256/HMAC in `transforms.ts` was already byte-parity); text construction matches Python (`" ".join` with "" for nulls, lower/strip inside the transform); scoring is BITWISE dice/jaccard over decoded filters; matches are clustered via the composite-id scheme (a→id·1e6, b→id·1e6+5e5, size≥2). Protocol semantics: `trusted_third_party` reports real scores; `smc` reveals only match bits (score == threshold) — Python's link_smc is likewise a simulation (real mp-spdz garbled circuits are a Python-side future enhancement); the TS `linkSMC` keeps its safety guards (shared `salt` HMAC key + non-"standard" level required). Fixture `pprl.json` (`emit_pprl_fixture.py`): 10 CLK byte-parity cases (plain/parametric/HMAC/presets/balanced-padding/empty) + both linkage modes on a margin-verified dataset (every pair dice ≥1e-3 from threshold, so Python's float32 matmul vs TS float64 can't flip a match). Gap note: number formatting differs for float fields (`str(5.0)`="5.0" vs `String(5.0)`="5") — fixtures use string fields; cast floats to strings before PPRL if cross-language CLK equality matters.
- **Cross-language fixtures:** committed under `tests/parity/fixtures/`. Regen via `packages/python/goldenmatch/tests/parity/memory/gen_memory_fixtures.py --rebuild-db` and the wave-specific emitters in `packages/python/goldenmatch/scripts/emit_ts_parity_fixtures.py`. Determinism clamp: pinned UUIDs, pinned `created_at` (no `datetime.now()`).
- **Negative-evidence parity** (v0.7.0): 6 fixture datasets exercising Path Y filtering on exact MKs + weighted-MK NE. Live in `tests/parity/negative-evidence-fixtures.json`.
- **Controller parity** (v0.5.0): structural-only on 4 of 6 fixtures, byte-equal on 2. Python-side `ModuleNotFoundError` on polars/sklearn in the divergent 4 — TS doesn't replicate that import wart.
- **Config-edit vocabulary parity** (Wave 4, 2026-06-05): `src/core/config-edits.ts` ports `core/config_edits.py` (6 edit types + `editFromSpec`/`parseLlmEdits`/`foldEdits` — the optimizer/LLM-repair lever language). Fixture `tests/parity/fixtures/config-edits.json` is emitted by `packages/python/goldenmatch/scripts/emit_config_edits_fixture.py` (17 edit-spec cases + a fold case); `tests/parity/config-edits.test.ts` must match Python's labels, apply/skip decisions, and semantic projections. Pydantic revalidation maps to explicit TS checks — `VALID_SCORERS` for `ScorerSwap`, and the BlockingConfig strategy/keys rules (`static`/`adaptive` need keys-or-subBlockKeys; `multi_pass` needs keys-or-passes) for blocking edits. The fixture caught exactly this: removing the last blocking key must be SKIPPED (invalid config), not applied.
- **Config optimizer parity** (Wave 4, 2026-06-05): `src/core/config-optimizer.ts` ports `core/config_optimizer.py`'s deterministic core — `GridProposer` (single-round threshold sweep w/ collapsed-variant dedup), `CoordinateDescentProposer` (6 lever families off the best-so-far; default scorers now include `qgram` — the char q-gram Jaccard scorer was ported, matching Python's default sweep — though the parity fixture still pins an explicit scorer tuple without `qgram` as a determinism clamp), and the `optimizeConfig` loop (fingerprint dedup, maxRounds/maxTrials, ties resolve toward "baseline"). Objectives: `"f1"` (dedupe + `evaluateClusters` per trial) and `"custom"` (caller `scoreFn`); Python's `"confidence"` objective reads the controller's zero-label profile which TS doesn't carry — NOT ported (throws with guidance). `LLMProposer` not ported (pass a custom `Proposer`). Fixture `config-optimizer.json` (emit_config_optimizer_fixture.py): proposer candidate labels per round (scorer tuple pinned both sides) + a full grid-loop run on a **margin-verified dataset** — the emitter asserts every pair score sits ≥0.10 from every swept threshold so 4-decimal scorer parity can't flip a trial; TS must match per-trial f1, best label, rounds.

## Public API surface (v0.8.0)
- `dedupeFile`, `dedupe`, `matchFile`, `match` — all return Promises.
- `autoConfigureRows` (sync, single-pass) and `autoConfigureRowsIterate` (Promise, full controller).
- `AutoConfigController`, `RunHistory`, `ComplexityProfile`, `HealthVerdict`, `StopReason`.
- `NegativeEvidenceField`, `applyNegativeEvidence`, `applyNegativeEvidenceToExactPairs`, `promoteNegativeEvidence`.
- Memory mirror: `getMemory`, `addCorrection`, `learn`, `memoryStats`.
- **Identity Graph (v0.8.0, edge-safe core):** `InMemoryIdentityStore`, `newEntityId`, `findByRecord`, `getEntity`, `listEntities`, `findConflicts`, `history`, `manualMerge`, `manualSplit`, `resolveClusters`, `ResolveSummary`, `IdentityView`, types
- **Pipeline-driven population (Wave 4, 2026-06-05): `resolveClusters` ported.** `src/core/identity/resolve.ts` is the edge-safe port of Python `identity/resolve.py`'s core (dict/Map path): per cluster it decides create / absorb / merge from which existing identities cover the records (`store.lookupEntityIds` pre-flight), upserts nodes + records, records `same_as` edges from `pairScores`, emits an idempotent event log (`hasRunEvent`), and flags weak-bottleneck `conflicts_with` edges. record_id = `${source}:${pk}` when `sourcePkCol` set, else `recordFingerprint`. Returns a `ResolveSummary` (created/absorbedRecords/merged/edgesAdded/eventsEmitted/recordsUpserted/conflictsFlagged). **Parity is structural** (UUID entity ids): fixture `tests/parity/fixtures/resolve-clusters.json` is emitted by `packages/python/goldenmatch/scripts/emit_resolve_fixture.py` (3-run create→absorb→merge scenario) and `tests/parity/resolve-clusters.test.ts` asserts identical per-run summaries + final record→entity grouping. **Deferred vs Python (documented):** postgres bulk fast-path, SP-A `cluster_frames` path, legacy content-hash migration candidate, `controllerSnapshot`, batch-fingerprint. Not yet wired into the TS dedupe pipeline (callable directly); auto-wiring is a follow-up. (`IdentityNode`, `SourceRecord`, `EvidenceEdge`, `IdentityEvent`, `IdentityAlias`, `IdentityStatus`, `EventKind`, `EdgeKind`, `IdentityStore`).
- **Identity Graph (v0.9.0, persistent backend):** `SqliteIdentityStore` in `src/node/identity/`. Implements every `IdentityStore` method (19 total) against an SQLite file at `.goldenmatch/identity.db` (configurable). `better-sqlite3` is an optional peer dep. Schema is byte-identical to Python so cross-toolkit DBs round-trip.
- MCP tool count: 45 (19 base + 5 memory + 6 identity + 15 agent, incl. the healer's `review_config`). Description literal at `src/node/mcp/server.ts:7`. `tool_count` is derived from `TOOLS.length`, asserted dynamically in `tests/unit/mcp-server.test.ts` (no hardcoded count).
- **MCP bin (v0.12.0):** `src/node/mcp/server.ts` is exposed as the `goldenmatch-mcp` bin (tsup entry `node/mcp/server`); it already had the JSON-RPC stdio loop (`startMcpServer`) — v0.12.0 added the shebang + `require.main` guard + bin wiring so it's directly runnable.
- **TS-TUI boost/export wiring (Wave 2.3, 2026-06-05):** the ink TUI's Boost tab now persists y/n labels to Learning Memory via `addCorrection({decision: "approve"|"reject", source: "steward", path: options.memoryPath})` (skip writes nothing) instead of dropping them in local React state. The Export tab writes real files via the extracted, unit-testable `writeExports(result, "csv"|"json", dir)` (golden/dupes/unique through `writeCsv`/`writeJson`) instead of the old `setTimeout` stub. New `tui` CLI flags: `--memory-path`, `-o/--output-dir`; new `TuiOptions.memoryPath`/`.outputDir`. Test: `tests/unit/tui-export.test.ts` (writeExports round-trips CSV + JSON to a tmp dir; ink closures themselves aren't renderable without ink-testing-library).
- **Identity MCP tools (v0.13.0):** `src/node/mcp/identity-tools.ts` exposes the 6 identity tools (`identity_resolve`/`identity_list`/`identity_history`/`identity_conflicts`/`identity_merge`/`identity_split`) at parity with `goldenmatch/mcp/identity_tools.py`, composed into `TOOLS` and routed via `IDENTITY_TOOL_NAMES` in the server. snake_case wire format; backed by `SqliteIdentityStore` (test seam `__setIdentityStoreFactoryForTests` injects `InMemoryIdentityStore` so tests skip the better-sqlite3 peer dep).

## CLI parity (Wave 4, 2026-06-05)
- Added the `evaluate` command (`src/cli.ts`): `goldenmatch-js evaluate <files...> --ground-truth gt.csv [--col-a id_a --col-b id_b --min-f1 X]` — runs `dedupe`, loads GT via `loadGroundTruthPairs`, scores with `evaluateClusters(result.clusters, gt, allIds)`, prints P/R/F1 + TP/FP/FN, exits non-zero below `--min-f1` (CI gate, parity with Python `goldenmatch evaluate`). `evaluateClusters` takes a 3rd `allIds` arg (pass `rows.map((_, i) => i)`).
- Fixed the hardcoded `0.1.0` version in two spots (`.version()` + the `info` command) — now `import pkg from "../package.json" with { type: "json" }` (resolveJsonModule + Bundler resolution; tsup inlines it). The built bin reports the real package version. `compare-clusters`/`sensitivity` CLI deferred (need a cluster-file loader / sweep plumbing).

## Build outputs
- tsup with 5 entry points: `index`, `core/index`, `node/index`, `cli`, `node/backends/score-worker` (piscina worker).
- Build artifacts to `dist/` (gitignored).
- Test count discipline: bump when adding parity datasets so future audits can diff.

## Config-types invariants
- **No `make*` factory functions** for config types — test fixtures use full literals. Required fields:
  - `MatchkeyField`: `field` + `transforms` + `scorer` + `weight`
  - `BlockingKeyConfig`: `fields` + `transforms`
  - `BlockingConfig`: `strategy` + `keys` + `maxBlockSize` + `skipOversized`
- **Scorer names are snake_case** (same as Python): `token_sort`, `record_embedding`, `soundex_match`, `ensemble`, `exact`, `jaro_winkler`, `levenshtein`.
- **`DOMAIN_EXTRACTED_COLS`** (in `src/core/domain.ts`) has only 3 entries (`__brand__`, `__model__`, `__version__`); Python's has 12. v0.11.0 ported the extractor *functions* (`extractSoftwareFeatures`/`extractBiblioFeatures`/`detectProductSubdomain`) but did NOT expand this producible-cols registry, so config auto-repair producibility is not yet at parity. Don't assume parity when porting domain features.

## Vitest gotchas
- Default timeout 5s. Heavier integration tests (PPRL multi-level, postflight end-to-end) need `{ timeout: 15000 }`. CI concurrent load has bitten this (cost a release: v0.3.0 → v0.3.1).

## Publish workflow
- `.github/workflows/publish-goldenmatch-js.yml` at monorepo root. Triggers on `goldenmatch-js-v*` tag or `workflow_dispatch` with `ref` input.
- Tag MUST point at a commit that has the workflow file, otherwise the trigger doesn't fire (root CLAUDE.md "Workflow trigger ordering" gotcha).
- Uses `NPM_TOKEN` secret. Trusted publishing not configured.
- The tag-version-must-match-package.json check (in the workflow) means you cannot tag multiple versions at the same commit. Each release commit has its own version bump and tag.
