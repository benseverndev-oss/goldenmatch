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
## Native HNSW ANN via wasm (`goldenmatch/core/hnsw-wasm`)
The `WasmHNSWANNBlocker` runs the SAME `goldenhnsw` Rust kernel as the Python
`goldenmatch-hnsw` wheel and the Rust core — compiled to wasm, so the
inner-product ANN ranking is byte-identical across Python / Rust / TS. Unlike
the `HNSWANNBlocker` (which wraps the Node-only `hnswlib-node` native addon),
this is **edge-safe**: no `node:*`, no native peer dep, runs in browsers /
Workers / edge. Loader `src/core/hnswWasm.ts`, exposed as the **opt-in subpath**
`goldenmatch/core/hnsw-wasm` (a separate tsup entry so the ~62 KB inlined wasm
never bloats the default `core` bundle — verified: `dist/core/index.js` carries
no wasm bytes).
- **Same synchronous inlined-wasm pattern as autoconfig-wasm** (`initSync` over a
  committed base64 wasm; the `.wasm` IS committed under `src/core/_wasm/` so
  `tsc`/`vitest`/`tsup` need no rust toolchain).
- **Regenerate the embed** (after any `goldenhnsw`/`goldenhnsw-wasm` change):
  `node scripts/build_goldenhnsw_wasm.mjs` (needs wasm-pack + the wasm32 target).
  Rebuilds the wasm, strips the wasm-bindgen async init path, base64-embeds it,
  and copies the golden vector into `tests/parity/fixtures/hnsw/`.
- **Cross-surface parity gate:** `tests/parity/hnsw.parity.test.ts` runs the SAME
  golden fixture as Rust (`goldenhnsw/tests/golden.rs`) + Python — ids match
  exactly, scores to f32. The CI `typescript` lane's HNSW drift guard (gated on
  the `hnsw_wasm` path filter) rebuilds + diffs the golden JSON, catching a stale
  committed wasm behaviorally.
- Scores are the raw inner product (cosine on L2-normalized embeddings) — same
  contract as the Python HNSW / FAISS `IndexFlatIP` path. Inner-product only; the
  `metric` option is accepted for interface symmetry (no euclidean).

## Record fingerprint via wasm (`goldenmatch/core/fingerprint-wasm`)
`recordFingerprint` (`src/core/record-fingerprint.ts`) computes the canonical
cross-surface stable record-id hash. It was the ONLY surface still hand-rolling
its own canonicalizer — Postgres is native-direct over `fingerprint-core`, and
DuckDB's `goldenmatch_record_fingerprint` calls the native-gated Python
`record_fingerprint` (native-authoritative when the wheel is present). Same
synchronous inlined-wasm pattern as hnsw-wasm; opt-in subpath
`goldenmatch/core/fingerprint-wasm` (~155 KB inlined wasm, separate tsup entry —
verified no leak into `dist/core/index.js`).
- **The kernel takes a JSON object string** (`fingerprint-core::fingerprint_json`,
  the same entry the SQL surfaces use). `recordFingerprint` routes a record
  through it **only when JSON-primitive-safe** (null/boolean/string/finite
  numbers with safe-int/no-`-0` semantics, ASCII field names); `bigint` /
  `Uint8Array` / `-0` / non-ASCII-key records — which a JSON round-trip can't
  reproduce byte-for-byte — stay on the pure-TS canonicalizer, which is the
  reference the wasm kernel matches for everything else. A `JSON.stringify` throw
  also falls back. So the hash is UNCHANGED whether or not the backend is enabled.
- **Enable it:** `import { enableFingerprintWasm } from "goldenmatch/core/fingerprint-wasm"`.
  Default-off (backend null → pure-TS), mirroring Python's native gate.
- **Regenerate the embed** (after any `fingerprint-core`/`fingerprint-wasm`
  change): `node scripts/build_fingerprint_wasm.mjs` (needs wasm-pack + the
  wasm32 target). Rebuilds the wasm, strips the async init path, base64-embeds it,
  and copies the golden into `tests/parity/fixtures/fingerprint/`.
- **Cross-surface parity gate:** `tests/parity/fingerprint-wasm.parity.test.ts`
  runs the SAME `fingerprint_golden.json` oracle as Rust
  (`fingerprint-core/tests/golden.rs`) + Python; `tests/unit/fingerprint-wasm-
  reroute.test.ts` proves wasm == pure-TS. The `typescript` lane's fingerprint
  drift guard (gated on the `fingerprint_wasm` path filter) rebuilds + diffs the
  golden JSON; `fixture_drift` auto-covers it too.

## In-house embedder via wasm (`goldenmatch/core/goldenembed-wasm`)
Edge embedding — char n-gram featurize + the learned linear projection head —
running the SAME `goldenembed-core` kernels as the Python native path and the SQL
surfaces. This is the P10 unblock: the `goldenembed` native runtime links `ort`
(ONNX Runtime, no wasm32), but the ONNX graph is just a linear projection
(`L2norm((feats @ W) + b)`), so the pure-Rust `goldenembed-core` does it with a
matmul and compiles to wasm. Loader `src/core/goldenembedWasm.ts`; opt-in subpath
(~80 KB inlined wasm, separate tsup entry — verified no leak into `dist/core/
index.js`).
- **`createEmbedder(model)`** builds an `Embedder` from the projection weights (a
  `(nFeatures*dim)` `Float32Array`, optional `dim` bias) + featurizer params (as
  saved by `GoldenEmbedModel.save`: `weights.npz` + `config.json`). `embed(texts)`
  returns a row-major `(n*dim)` `Float32Array`, each row L2-normalized. The model
  is caller-supplied — the wasm carries only the kernel, not any weights.
- **Cosine-tolerance parity, NOT byte-identity** — f32 matmul accumulation order
  differs from numpy/ONNX (which already differ from each other at this scale),
  and the output feeds thresholded cosine blocking. Worst cosine distance 1.8e-7
  vs the numpy reference. Same synchronous inlined-wasm pattern as the others.
- **Regenerate the embed** (after any `goldenembed-core`/`goldenembed-wasm`
  change): `node scripts/build_goldenembed_wasm.mjs` (wasm-pack + wasm32 target).
- **Parity gate:** `tests/parity/goldenembed-wasm.parity.test.ts` runs the shared
  `project_golden.json` oracle (also checked by `goldenembed-core/tests/
  project_parity.rs`); the `typescript` lane's goldenembed drift guard (gated on
  `goldenembed_wasm`) rebuilds + diffs it; `fixture_drift` auto-covers it.

## Fellegi-Sunter block scoring via wasm (`goldenmatch/core/fs-wasm`)
FS block scoring — the per-pair level-band → EM match-weight → normalize kernel —
running the SAME `goldenmatch-fs-core::score_fs_pair` as the Python native wheel
and the native pyo3 crate. This is the TS half of the 2026-07-17 fs-core
cross-surface extraction: FS scoring was the parity orphan (numpy + scalar +
native + hand-written TS, synced by hand); the shared pyo3-free core makes
Python-native == TS-WASM byte-identical by construction. Loader
`src/core/fsWasm.ts`; opt-in subpath (~187 KB inlined wasm, separate tsup entry —
verified no leak into `dist/core/index.js`).
- **`scoreBlockPairsFs(input)`** takes a pre-trained, pre-transformed block (the
  JSON-boundary shape the native `score_block_pairs_fs` kernel takes:
  `fieldValues[field][row]` with `null` = unobserved, per-field scorer/level/
  weight arrays, calibration + normalization scalars) and returns `[a,b,score]`
  triples `a<b` at/above `threshold`. EM training + transforms stay host-side
  (exactly as they stay Python-side). This entry covers the zero-config FS shape
  (no NE, no custom banding, no cross-batch exclude — what
  `auto_configure_probabilistic_df` emits); NE / custom `level_thresholds` grow
  from here, like the native kernel. **The FS scoring path reroute (making the TS
  probabilistic scorer call this) is a deliberate follow-up** — this ships the
  kernel + parity, mirroring how hnsw-wasm shipped as an alternative surface first.
- **Same synchronous inlined-wasm pattern** as hnsw-wasm/autoconfig-wasm (`initSync`
  over a committed base64 wasm under `src/core/_wasm/`; `tsc`/`vitest`/`tsup` need
  no rust toolchain). Regenerate the embed (after any `fs-core`/`fs-wasm` change):
  `node scripts/build_fs_wasm.mjs` (needs wasm-pack + the wasm32 target).
- **Cross-surface parity gate:** `tests/parity/fs-wasm.parity.test.ts` feeds the
  SAME inputs the Python NATIVE kernel scored — the fixture
  `tests/parity/fixtures/fs/fs_block_scoring.json` is AUTHORED by the native
  `score_block_pairs_fs` (the oracle,
  `packages/python/goldenmatch/scripts/emit_fs_wasm_fixture.py`), NOT copied by the
  build script — and asserts identical pairs (scores pinned to the fixture's 6dp).
  `fixture_drift` auto-covers the build script; the fs-core + fs-wasm crates run
  `cargo test`/`clippy` in the `rust` lane.

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
- **Agent tools `sensitivity` / `incremental` are NO LONGER Python-only** — both were
  ported to TS on 2026-07-23 (Tier 3 PR-4): `src/core/incremental.ts` (ports
  `run_incremental`) and the Python-faithful sweep engine in `src/core/sensitivity.ts`
  (`runSensitivitySweep` / `sweepStabilityReport`, ports `run_sensitivity`). Both are now
  `shared` base MCP tools (see the MCP-tool-count note below). This reverses the
  2026-06-15 "declared, not a gap" Python-only decision. They remain
  `a2a_skills.python_only` — base MCP tools do NOT feed the TS A2A card, so the TS A2A
  surface still does not advertise them (Python's A2A does). `certify_recall` was
  likewise reversed in Tier 3 PR-3 (`src/core/recall-certificate.ts`). No goldenmatch
  agent tools remain Python-only after this PR.

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
- MCP tool count: **79** (base + aliases + 6 memory + 8 identity + 15 agent + `convert_splink_config` + `compare_clusters` + `schema_match` + `config_weaknesses` + `analyze_blocking` + `certify_recall` + `incremental` + `sensitivity` + `retrieve_similar` + 7 run tools + 2 rollback tools + 2 surgery tools). Header literal at `src/node/mcp/server.ts:7`; `server_info.tool_count` is derived from `TOOLS.length`, and `tests/unit/mcp-server.test.ts` pins `TOOLS.length` (bump it when adding a tool). `compare_clusters` (CCMS cluster comparison) was wired 2026-07-23 as a **stateless** tool (two cluster-JSON file paths → CCMS summary), flipping it to `shared` in `parity/goldenmatch.yaml`; its core (`compareClusters` + `ccmsSummary`/`parseClustersJson` in `src/core/compare-clusters.ts`) already existed.
- **`retrieve_similar` MCP tool (2026-07-23, Tier 3 final, 71 → 72).** Class-B net-new edge-safe core port — the LAST buildable Tier 3 tool (only the deferred identity-audit trio remains). `retrieve_similar` (stateless, reads a CSV `file_path`) ports `core/retrieval.py::retrieve_similar_records` into `src/core/retrieve-similar.ts` — embed the chosen column + the free-text query, ANN cosine search via the existing edge-safe `ANNBlocker` (**no new ANN impl**), optional `{column: value}` equality pre-filter before embedding, top-`k` over `threshold`; Python-parity response `{file, query, column, count, results:[{row_id, score, record}]}`. **CALLER-SUPPLIED EMBEDDER (edge-model caveat):** Python defaults to the bundled zero-config `"inhouse"` model; the TS surface carries only the embedding KERNEL (`goldenembed-wasm`), NOT a model, so `retrieveSimilar` REQUIRES an explicit `embedder` and throws `RetrieveSimilarError` when none is given — no silent default. The node handler requires a `provider` (openai/vertex/voyage) arg + credentials, builds the embedder via the existing `getEmbedder`, and errors clearly if the provider is missing. Flips `python_only → shared` under `mcp_tools`. **a2a_skills unchanged** — base MCP tools do NOT feed the TS A2A card (built from BASE_SKILLS + AGENT_SKILLS + MEMORY_TOOLS + IDENTITY_TOOLS), so `retrieve_similar` stays `a2a_skills.python_only` (Python A2A exposes it; TS does not) — verified via `scripts/emit_ts_surface.mjs`. Tests: `tests/unit/retrieve-similar.test.ts`.
- **`schema_match` + `config_weaknesses` MCP tools (2026-07-23, Tier 3 PR-2, 65 → 67).** Class-B net-new edge-safe core ports. `schema_match` (stateless, two file paths → column mapping) ports `core/schema_match.py::auto_map_columns` into `src/core/schema-match.ts`, **reusing the existing `jaroWinkler` kernel** for the reference-string similarity (no new similarity impl); output is the Python-parity snake_case mapping shape (`col_a`/`col_b`/`score`/`method`/`composite_cols`). `config_weaknesses` (reads the current `RUN_STORE` run) ports `core/config_critique.py::diagnose_config` into `src/core/config-critique.ts` — the deterministic detectors (source/id admitted, oversized/shared-value block, over-merge, distributed-over-merge, null-sink, low-signal-key) + the template summary; the optional `GOLDENMATCH_WEAKNESS_LLM` summary is NOT ported (offline template is the default). Both flip `python_only → shared` under `mcp_tools`. **a2a_skills is unchanged** — base MCP tools do NOT feed the TS A2A card (built from BASE_SKILLS + AGENT_SKILLS + MEMORY_TOOLS + IDENTITY_TOOLS), exactly like PR-1's `lineage` (a RUN_TOOL): `schema_match` stays `python_only`, `config_weaknesses` stays absent from `a2a_skills`. Tests: `tests/unit/schema-match.test.ts`, `tests/unit/config-critique.test.ts`.
- **`analyze_blocking` + `certify_recall` MCP tools (2026-07-23, Tier 3 PR-3, 67 → 69).** Class-B net-new edge-safe core ports. `analyze_blocking` (reads the current `RUN_STORE` run) ports `core/block_analyzer.py::analyze_blocking` into `src/core/block-analyzer.ts` — column-type-heuristic candidate generation + compound pairs, block-size-distribution scoring (`group_count`/`max_group_size`/sample-std/`total_comparisons`=Σn(n-1)/2/composite score), top-10 recall estimation via JaroWinkler pair sampling (**reuses the existing `jaroWinkler` kernel**, no new similarity impl), coverage demotion; returns Python-parity `{matchkey_columns, suggestions}` (`suggestions` = `asdict(BlockingSuggestion)`). `certify_recall` (stateless, reads a CSV `file_path`) ports `core/recall_certificate.py::certify_recall_df` + `estimate_recall` into `src/core/recall-certificate.ts` — auto-config → `buildDecorrelatedSystems` (≥3 systems, splitting a wide matchkey per-field when <3) → dedupe per system → `clustersToPairs` → FP-aware **capture-recapture** estimator (`recall = 1−(1−p)^K`, `p` from the slope of `log f_k − log C(K,k)` over the FP-free `k≥2` cells). **SEMANTIC LANDMINE: the result is a LOWER-BOUND POINT ESTIMATE treating each pass as a decorrelated system — NOT a supervised/ground-truth recall number.** Python's `note` framing is preserved verbatim; response is Python-parity `{estimated_recall, n_systems, found_pairs, system_overlap, estimable, note}`. Both flip `python_only → shared` under `mcp_tools`. **BOUNDARY-PROSE REVERSAL:** `certify_recall` was previously "Python-only by design" (see "Deliberately not ported") — this PR reverses that; only `sensitivity`/`incremental` remain Python-only agent tools. **a2a_skills unchanged** — base MCP tools do NOT feed the TS A2A card: `analyze_blocking` stays `python_only` (Python A2A exposes it, TS does not), `certify_recall` stays absent from `a2a_skills`. Tests: `tests/unit/block-analyzer.test.ts`, `tests/unit/recall-certificate.test.ts`.
- **`incremental` + `sensitivity` MCP tools (2026-07-23, Tier 3 PR-4, 69 → 71).** Class-B net-new edge-safe core ports; both flip `python_only → shared` under `mcp_tools`. `incremental` (stateless, reads `base_file` + `new_records`) ports `core/incremental.py::run_incremental` into `src/core/incremental.ts` — stamps base rows `__row_id__` 0..h-1 / new rows offset above the base max, standardizes + computes matchkeys on the combined frame, then **splits exact vs fuzzy matchkeys exactly as Python does**: EXACT matchkeys resolve via `findExactMatches` (a hash equijoin over the combined frame, cross-source-filtered) and FUZZY matchkeys via per-new-record `matchOne` (reuses the existing `src/core/match-one.ts`). Dedups best-score per `(new,base)` pair; returns Python-parity `{base_records, new_records, matched_to_base, new_entities, total_pairs, matches:[{new_row_id, base_row_id, score}]}`. **LANDMINE:** routing every matchkey through `matchOne` (or forgetting the exact matchkeys) silently drops exact-only matches — the exact path is a separate hash join. (Note: TS `matchOne` scores exact fields at threshold 1.0 rather than returning `[]` like Python's `match_one`, but the faithful split via `findExactMatches` is kept for correct composite/null semantics + O(n) hashing.) `sensitivity` (reads `file_path` + `sweep` specs) ports `core/sensitivity.py::run_sensitivity` into the Python-faithful sweep engine added to `src/core/sensitivity.ts` (`runSensitivitySweep` + `sweepStabilityReport` + `SweepSpec`/`SweepPointResult`/`SweepResult` — ADDED ALONGSIDE the pre-existing Cartesian `runSensitivity`/`stabilityReport`, which stay untouched). Each param sweeps independently over a `start:stop:step` range; every run is compared to ONE baseline clustering via **CCMS** (`compareClusters`, NOT re-implemented). **LANDMINE:** per-point errors are caught so PARTIAL RESULTS survive (a failing sweep point is skipped, not fatal), and `unchanged %` is measured against the baseline. Wire shape matches Python's `stability_report`: `{results:[{best_value, best_unchanged_pct, points:[{value, unchanged, merged, partitioned, overlapping, twi}]}]}`. **BOUNDARY-PROSE REVERSAL:** both were "Python-only by design" (see "Deliberately not ported") — this PR reverses that. **a2a_skills unchanged** — base MCP tools do NOT feed the TS A2A card, so both stay `a2a_skills.python_only`. Tests: `tests/unit/incremental.test.ts`, `tests/unit/sensitivity-sweep.test.ts`.
- **`memory_import` + `lineage` MCP tools (2026-07-23, Tier 3 PR-1, 63 → 65).** Both are class-A wiring on state layers that already existed. `memory_import` (in `MEMORY_TOOLS`/`memory-tools.ts`, the inverse of `memory_export`) writes each supplied correction dict via `SqliteMemoryStore.addCorrection` — so the trust upsert (incoming trust < existing ⇒ ignore) applies for free — preserving `record_hash`/`field_hash` VERBATIM (never regenerated; `applyCorrections` re-anchors them and `record_hash` excludes `__row_id__`, so regenerating would break durability). Default `source="api"` ⇒ trust 0.5 via `trustForSource`; response `{imported}` counts rows processed (Python parity). `lineage` (in `RUN_TOOLS`/`run-tools.ts`) reads the current run from `RUN_STORE` and calls the edge-safe `core/lineage.ts::buildLineage(result)` — zero net-new core; input `{max_pairs, natural_language}`, response `{count, lineage}` (Python-shaped `{error: "No dataset loaded"}` when no run is loaded). Both flip `python_only → shared` in `parity/goldenmatch.yaml`. Tests: `tests/unit/mcp-memory-tools.test.ts` (verbatim-hash + trust-upsert), `tests/unit/mcp-run-tools.test.ts` (lineage per golden record + no-run error).
- **`agent_approve_reject` is now FUNCTIONAL (2026-07-23, count UNCHANGED at 63).** The tool was always advertised but its `src/core/agent/skills.ts` handler was a `{recorded: true}` no-op (a "Wave-3+ follow-up" stub) that silently discarded the decision. It now PERSISTS a durable `Correction` to Learning Memory, faithful to Python's `_write_agent_correction` (`source='agent'`, `trust=0.5`, empty field/record hashes, `original_score` 0.0, pair canonicalized to `(min, max)`). Mechanism: the agent `SkillContext` gained an optional `openMemoryStore` factory + `dataset`; the node surface (`src/node/mcp/agent-tools.ts::handleAgentTool`, shared by MCP + A2A) supplies it via `SqliteMemoryStore` (`.goldenmatch/memory.db`, `path` override) — only `agent_approve_reject` invokes it, so no store opens for unrelated skills. Edge path (no store wired) returns the decision without persisting, matching Python's `memory_store=None`. Response shape is now Python-parity `{status, decision, job_name?, id_a, id_b, decided_by?}` (was `{recorded: true, ...}`). Stays `ts_only` on the A2A card + `shared` on the MCP surface in `parity/goldenmatch.yaml` (both surfaces already exposed a working tool) — no manifest move. Tests: `tests/unit/agent-skills.test.ts` (persistence for approve + reject via an in-memory `MemoryStore`).
- **Server-held run store (2026-07-23, Tier 1 of `docs/superpowers/specs/2026-07-23-ts-mcp-stateful-run-store-design.md`).** `src/node/mcp/run-store.ts` (`RUN_STORE`, a bounded/TTL-evicted store keyed by run id, mirroring Python's `_session_store.py` bounds — `GOLDENMATCH_MCP_SESSION_MAX`=64 / `GOLDENMATCH_MCP_SESSION_TTL`=3600) is populated by the `dedupe` case (which now also returns a `run_id`) and read by the 6 **run tools** in `src/node/mcp/run-tools.ts`: `get_stats`/`list_clusters`/`get_cluster`/`get_golden_record`/`export_results` (read the current run) + the stateless file-stager `upload_dataset` — all flipped `python_only → shared`. State lives in `src/node/**` (the MCP server is node-only), so the edge-safe core stays pure. `sanitizePath` was extracted to `src/node/mcp/paths.ts` (shared with run-tools). `sensitivity` remains deliberately Python-only + unadvertised (see "Deliberately not ported"); `certify_recall` was Python-only through Tier 3 PR-2 but is now ported + `shared` (Tier 3 PR-3).
- **Rollback subsystem tools (2026-07-23, `list_runs` + `rollback`).** `src/node/mcp/run-log.ts` is a faithful port of Python's `core/rollback.py`: `saveRunSnapshot`/`listRuns`/`rollbackRun` over an on-disk `.goldenmatch_runs.json` log (append, keep last 50, mark-and-rewrite). This is a **separate, durable** state layer from the ephemeral in-memory `RUN_STORE` — rollback needs a persistent record of which output *files* a run wrote, so `RUN_STORE` (TTL-evicted, lost on restart) can't back it. The 2 tools live in `src/node/mcp/rollback-tools.ts` (`ROLLBACK_TOOLS`/`ROLLBACK_TOOL_NAMES`/`handleRollbackTool`), wired into `server.ts`, and flip `python_only → shared` in `parity/goldenmatch.yaml` (57 → 59 tools). Path jailing reuses `sanitizePath` (mirrors Python's `safe_path`: a jailed/missing output file is reported under `not_found`, never thrown out of the call). **Writer parity:** `saveRunSnapshot` is a callable NOT auto-wired into the pipeline — Python's own pipeline never calls `save_run_snapshot` either (only tests do); wiring a snapshot writer into `dedupe()`/`export_results` is a deliberate cross-surface follow-up.
- **Cluster-surgery run tools (2026-07-23, Tier 2, `unmerge_record` + `shatter_cluster`).** `src/node/mcp/surgery-tools.ts` (`SURGERY_TOOLS`/`SURGERY_TOOL_NAMES`/`handleSurgeryTool`) ports Python's `_tool_unmerge_record`/`_tool_shatter_cluster` (which delegate to `MatchEngine.unmerge_record`/`unmerge_cluster`). These MUTATE THE CURRENT RUN IN PLACE via a new `RunStore.update(runId, newResult)` method (replaces the stored run's `result` while preserving its `runId`/`createdAt`/`rowsById`/`sourcePath`/insertion order/current pointer) — surgery edits an existing run, so it must NOT `put` a new run id. Flow: read current run → shallow-copy `result.clusters` (mutable, `pairScores` intact) → `unmergeRecord`/`unmergeCluster` (the edge-safe kernels in `core/cluster.ts`, which RETURN a new Map) → `rebuildResult` recomputes stats the pipeline way (`totalClusters = clusters.size`, `matchedRecords` = members of size≥2 clusters) → `RUN_STORE.update(...)`. `unmerge_record` re-clusters the remainder from the stored `pairScores` (no re-scoring); `shatter_cluster` breaks a cluster into singletons (pair scores discarded). **Memory-write parity:** both kernels accept an OPTIONAL `memoryStore` that auto-writes `reject` corrections; Python's MCP path passes none, so the TS wiring OMITS it too (no auto-emitted corrections). Flips `python_only → shared` in `parity/goldenmatch.yaml` (59 → 61 tools). Tests: `tests/unit/mcp-surgery-tools.test.ts` (record pulled + remainder re-clustered via pairScores, members→singletons, runId preserved across the in-place mutation, no-run/unknown-id error paths).
- **MCP bin (v0.12.0):** `src/node/mcp/server.ts` is exposed as the `goldenmatch-mcp` bin (tsup entry `node/mcp/server`); it already had the JSON-RPC stdio loop (`startMcpServer`) — v0.12.0 added the shebang + `require.main` guard + bin wiring so it's directly runnable.
- **TS-TUI boost/export wiring (Wave 2.3, 2026-06-05):** the ink TUI's Boost tab now persists y/n labels to Learning Memory via `addCorrection({decision: "approve"|"reject", source: "steward", path: options.memoryPath})` (skip writes nothing) instead of dropping them in local React state. The Export tab writes real files via the extracted, unit-testable `writeExports(result, "csv"|"json", dir)` (golden/dupes/unique through `writeCsv`/`writeJson`) instead of the old `setTimeout` stub. New `tui` CLI flags: `--memory-path`, `-o/--output-dir`; new `TuiOptions.memoryPath`/`.outputDir`. Test: `tests/unit/tui-export.test.ts` (writeExports round-trips CSV + JSON to a tmp dir; ink closures themselves aren't renderable without ink-testing-library).
- **Identity MCP tools (v0.13.0):** `src/node/mcp/identity-tools.ts` exposes the identity tools at parity with `goldenmatch/mcp/identity_tools.py`, composed into `TOOLS` and routed via `IDENTITY_TOOL_NAMES` in the server. snake_case wire format; backed by `SqliteIdentityStore` (test seam `__setIdentityStoreFactoryForTests` injects `InMemoryIdentityStore` so tests skip the better-sqlite3 peer dep).
- **Identity-conflict MCP tools (2026-07-23, `identity_claim` + `identity_resolve_conflict`, TS↔Python parity).** These complete the identity mutation set (`identity_merge`/`identity_split` already shipped), taking the identity tool group 6 → 8 and the whole MCP surface **61 → 63**; both flip `python_only → shared` in `parity/goldenmatch.yaml`. They are class-B net-new core ports — the backing TS core functions did NOT exist and were ported here:
  - `claimRecord` (`src/core/identity/query.ts` ← Python `query.py::claim_record`): reassigns a record to a target entity, emitting a `claimed` event on BOTH the gaining and losing entities. Idempotent — claiming a record already in the target is a no-op (`moved: false`, no events), so a replay does nothing. Added `"claimed"` to the `EventKind` union.
  - `mediateConflict` + `openConflicts` (`src/core/identity/mediation.ts` ← Python `mediation.py`): adjudicate a `conflicts_with` pair. `same` keeps the entity, `distinct` splits `record_b` out via `manualSplit`, `defer` logs only. Each verdict is a durable `mediation_verdict` evidence edge (resolution in `negativeEvidence`) + a `conflict_mediated` event. Added `"mediation_verdict"` to `EdgeKind` and `"conflict_mediated"` to `EventKind`.
  - New store method **`edgesByKind(kind, dataset?)`** on the `IdentityStore` interface + BOTH backends (`InMemoryIdentityStore` and `SqliteIdentityStore`) — the generic counterpart to `findConflicts`, ports Python `store.edges_by_kind`; `open_conflicts` depends on it. Returns edges newest-first with a `recordedAt DESC, edgeId DESC` tiebreak so latest-wins verdict lookups are deterministic.
  - **Re-mediation trick preserved:** each `mediateConflict` mints a unique `mediation:<iso>:<seq>` run_name so re-adjudicating the SAME pair appends a NEW verdict edge instead of silently no-op'ing against the edge `UNIQUE(entity, a, b, kind, run_name)` constraint. All edge lookups canonicalize via `canonRecordPair`.
  - These MUTATE the durable identity graph via `SqliteIdentityStore` (`.goldenmatch/identity.db`) — the node-only, stateful, durable-SQLite-backed surface, NOT the edge-safe core (the new core fns are edge-safe; only the persistence backend is node-only). They do NOT touch the ephemeral `RUN_STORE`. Tests: `tests/unit/identity-mediation.test.ts` (edge-safe core on `InMemoryIdentityStore`), `tests/node/identity/sqlite-store.test.ts` (durable backend + `edgesByKind`/`openConflicts`), `tests/unit/mcp-identity-tools.test.ts` (the two tools' dispatch + snake_case wire).

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
