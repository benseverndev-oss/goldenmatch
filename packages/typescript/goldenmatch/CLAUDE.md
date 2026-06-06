# goldenmatch (TypeScript)

npm package `goldenmatch`. Parity port of the Python sibling at `packages/python/goldenmatch/`. Currently at **v0.11.0** (core-algorithm parity catch-up + Phase-5 golden-strategy plugin port). Python sibling is at v1.16; v1.13/v1.14/v1.16 are explicitly not-ported (see CHANGELOG.md for the per-version rationale).

> **Versioning note:** `package.json` was briefly set to `2.0.0` by #463 (the "Phase 5 plugin port" milestone label), but that broke the documented 0.x wave line and was never published (npm stayed at 0.10.0). Corrected to `0.11.0` for the release that ships the plugin port + the core-parity gap closure. The package stays intentionally pre-1.0.

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

Each wave's spec/plan: `docs/superpowers/specs/2026-05-10-ts-parity-arc-design.md` + per-wave plans.

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

Everything else (core scoring/blocking/clustering/golden, auto-config controller,
identity graph, PPRL, memory, MCP/A2A, CLI, connectors) IS ported — see the wave
history above. This closes the cross-surface parity roadmap (Waves 0–3, 5 shipped;
Wave 4 = this declaration).

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

## Parity contract
- **Scorer output:** 4-decimal tolerance vs Python (`tests/parity/scorer-ground-truth.test.ts`).
- **Hash bytes:** SHA-256 truncated to 16 hex via Web Crypto. UTF-8 mandatory. Hash input = values joined by `|` (NOT `<col>=<val>`). `__row_id__` excluded from `record_hash` so corrections survive row reordering.
- **Cross-language fixtures:** committed under `tests/parity/fixtures/`. Regen via `packages/python/goldenmatch/tests/parity/memory/gen_memory_fixtures.py --rebuild-db` and the wave-specific emitters in `packages/python/goldenmatch/scripts/emit_ts_parity_fixtures.py`. Determinism clamp: pinned UUIDs, pinned `created_at` (no `datetime.now()`).
- **Negative-evidence parity** (v0.7.0): 6 fixture datasets exercising Path Y filtering on exact MKs + weighted-MK NE. Live in `tests/parity/negative-evidence-fixtures.json`.
- **Controller parity** (v0.5.0): structural-only on 4 of 6 fixtures, byte-equal on 2. Python-side `ModuleNotFoundError` on polars/sklearn in the divergent 4 — TS doesn't replicate that import wart.
- **Config-edit vocabulary parity** (Wave 4, 2026-06-05): `src/core/config-edits.ts` ports `core/config_edits.py` (6 edit types + `editFromSpec`/`parseLlmEdits`/`foldEdits` — the optimizer/LLM-repair lever language). Fixture `tests/parity/fixtures/config-edits.json` is emitted by `packages/python/goldenmatch/scripts/emit_config_edits_fixture.py` (17 edit-spec cases + a fold case); `tests/parity/config-edits.test.ts` must match Python's labels, apply/skip decisions, and semantic projections. Pydantic revalidation maps to explicit TS checks — `VALID_SCORERS` for `ScorerSwap`, and the BlockingConfig strategy/keys rules (`static`/`adaptive` need keys-or-subBlockKeys; `multi_pass` needs keys-or-passes) for blocking edits. The fixture caught exactly this: removing the last blocking key must be SKIPPED (invalid config), not applied.
- **Config optimizer parity** (Wave 4, 2026-06-05): `src/core/config-optimizer.ts` ports `core/config_optimizer.py`'s deterministic core — `GridProposer` (single-round threshold sweep w/ collapsed-variant dedup), `CoordinateDescentProposer` (6 lever families off the best-so-far; default scorers omit `qgram` — not a TS scorer), and the `optimizeConfig` loop (fingerprint dedup, maxRounds/maxTrials, ties resolve toward "baseline"). Objectives: `"f1"` (dedupe + `evaluateClusters` per trial) and `"custom"` (caller `scoreFn`); Python's `"confidence"` objective reads the controller's zero-label profile which TS doesn't carry — NOT ported (throws with guidance). `LLMProposer` not ported (pass a custom `Proposer`). Fixture `config-optimizer.json` (emit_config_optimizer_fixture.py): proposer candidate labels per round (scorer tuple pinned both sides) + a full grid-loop run on a **margin-verified dataset** — the emitter asserts every pair score sits ≥0.10 from every swept threshold so 4-decimal scorer parity can't flip a trial; TS must match per-trial f1, best label, rounds.

## Public API surface (v0.8.0)
- `dedupeFile`, `dedupe`, `matchFile`, `match` — all return Promises.
- `autoConfigureRows` (sync, single-pass) and `autoConfigureRowsIterate` (Promise, full controller).
- `AutoConfigController`, `RunHistory`, `ComplexityProfile`, `HealthVerdict`, `StopReason`.
- `NegativeEvidenceField`, `applyNegativeEvidence`, `applyNegativeEvidenceToExactPairs`, `promoteNegativeEvidence`.
- Memory mirror: `getMemory`, `addCorrection`, `learn`, `memoryStats`.
- **Identity Graph (v0.8.0, edge-safe core):** `InMemoryIdentityStore`, `newEntityId`, `findByRecord`, `getEntity`, `listEntities`, `findConflicts`, `history`, `manualMerge`, `manualSplit`, `resolveClusters`, `ResolveSummary`, `IdentityView`, types
- **Pipeline-driven population (Wave 4, 2026-06-05): `resolveClusters` ported.** `src/core/identity/resolve.ts` is the edge-safe port of Python `identity/resolve.py`'s core (dict/Map path): per cluster it decides create / absorb / merge from which existing identities cover the records (`store.lookupEntityIds` pre-flight), upserts nodes + records, records `same_as` edges from `pairScores`, emits an idempotent event log (`hasRunEvent`), and flags weak-bottleneck `conflicts_with` edges. record_id = `${source}:${pk}` when `sourcePkCol` set, else `recordFingerprint`. Returns a `ResolveSummary` (created/absorbedRecords/merged/edgesAdded/eventsEmitted/recordsUpserted/conflictsFlagged). **Parity is structural** (UUID entity ids): fixture `tests/parity/fixtures/resolve-clusters.json` is emitted by `packages/python/goldenmatch/scripts/emit_resolve_fixture.py` (3-run create→absorb→merge scenario) and `tests/parity/resolve-clusters.test.ts` asserts identical per-run summaries + final record→entity grouping. **Deferred vs Python (documented):** postgres bulk fast-path, SP-A `cluster_frames` path, legacy content-hash migration candidate, `controllerSnapshot`, batch-fingerprint. Not yet wired into the TS dedupe pipeline (callable directly); auto-wiring is a follow-up. (`IdentityNode`, `SourceRecord`, `EvidenceEdge`, `IdentityEvent`, `IdentityAlias`, `IdentityStatus`, `EventKind`, `EdgeKind`, `IdentityStore`).
- **Identity Graph (v0.9.0, persistent backend):** `SqliteIdentityStore` in `src/node/identity/`. Implements every `IdentityStore` method (19 total) against an SQLite file at `.goldenmatch/identity.db` (configurable). `better-sqlite3` is an optional peer dep. Schema is byte-identical to Python so cross-toolkit DBs round-trip.
- MCP tool count: 30 (19 base + 5 memory + 6 identity). Description literal at `src/node/mcp/server.ts:7`. `tool_count` is derived from `TOOLS.length`, asserted dynamically in `tests/unit/mcp-server.test.ts` (no hardcoded count).
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
