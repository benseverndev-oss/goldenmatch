# goldenmatch (TypeScript)

npm package `goldenmatch`. Parity port of the Python sibling at `packages/python/goldenmatch/`. Currently at **v0.8.0** (Identity Graph edge-safe core, Python v1.15 partial). Python sibling is at v1.16; v1.13/v1.14/v1.16 are explicitly not-ported (see CHANGELOG.md for the per-version rationale).

## Wave history
| npm | Python parity | Headline |
|-----|---------------|----------|
| 0.4.0 | v1.6.0 | Learning Memory + scorer ground truth |
| 0.5.0 | v1.7 + v1.8 | AutoConfigController, ComplexityProfile, RunHistory, StopReason telemetry |
| 0.6.0 | v1.9 + v1.10 | 5 complexity indicators + indicator-aware refit rules; scorer selection aligned with Python |
| 0.7.0 | v1.11 + v1.12 | NegativeEvidenceField + Path Y (exact-MK post-filter) |
| 0.8.0 | v1.15 (partial) | Identity Graph edge-safe core (`InMemoryIdentityStore` + query helpers). Persistent SQLite backend + pipeline-driven population deferred to a future wave. |

Each wave's spec/plan: `docs/superpowers/specs/2026-05-10-ts-parity-arc-design.md` + per-wave plans.

## Deliberately not ported (Python deltas)
- **Python v1.13 (typed accessors).** TS strict mode (`noUncheckedIndexedAccess` + `exactOptionalPropertyTypes`) already enforces the same invariants at compile time.
- **Python v1.14 (controller surface-parity arc).** Threaded telemetry through TUI / CLI / Postgres / DuckDB surfaces that TS doesn't expose. TS already surfaces telemetry on its MCP server via the same `serialize_telemetry` JSON shape.
- **Python v1.16 (`backend="bucket"` 5M-on-one-node).** Polars-only Python path. TS runs edge-safe in Web Crypto and doesn't ship Polars — no TS analogue planned.

## Commands
```bash
cd packages/typescript/goldenmatch
pnpm --filter goldenmatch test      # vitest (854 tests at v0.8.0)
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

## Public API surface (v0.8.0)
- `dedupeFile`, `dedupe`, `matchFile`, `match` — all return Promises.
- `autoConfigureRows` (sync, single-pass) and `autoConfigureRowsIterate` (Promise, full controller).
- `AutoConfigController`, `RunHistory`, `ComplexityProfile`, `HealthVerdict`, `StopReason`.
- `NegativeEvidenceField`, `applyNegativeEvidence`, `applyNegativeEvidenceToExactPairs`, `promoteNegativeEvidence`.
- Memory mirror: `getMemory`, `addCorrection`, `learn`, `memoryStats`.
- **Identity Graph (v0.8.0, edge-safe core):** `InMemoryIdentityStore`, `newEntityId`, `findByRecord`, `getEntity`, `listEntities`, `manualMerge`, `manualSplit`, `IdentityView`, types (`IdentityNode`, `SourceRecord`, `EvidenceEdge`, `IdentityEvent`, `IdentityAlias`, `IdentityStatus`, `EventKind`, `EdgeKind`, `IdentityStore`).
- MCP tool count: 24 (19 base + 5 memory). Description literal at `src/node/mcp/server.ts:6` — keep in sync via the existing regex test. Identity MCP tools will be added alongside the persistent backend in a future wave.

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
- **`DOMAIN_EXTRACTED_COLS`** (in `src/core/domain.ts`) has only 3 entries (`__brand__`, `__model__`, `__version__`); Python's has 12. Don't assume parity when porting domain features.

## Vitest gotchas
- Default timeout 5s. Heavier integration tests (PPRL multi-level, postflight end-to-end) need `{ timeout: 15000 }`. CI concurrent load has bitten this (cost a release: v0.3.0 → v0.3.1).

## Publish workflow
- `.github/workflows/publish-goldenmatch-js.yml` at monorepo root. Triggers on `goldenmatch-js-v*` tag or `workflow_dispatch` with `ref` input.
- Tag MUST point at a commit that has the workflow file, otherwise the trigger doesn't fire (root CLAUDE.md "Workflow trigger ordering" gotcha).
- Uses `NPM_TOKEN` secret. Trusted publishing not configured.
- The tag-version-must-match-package.json check (in the workflow) means you cannot tag multiple versions at the same commit. Each release commit has its own version bump and tag.
