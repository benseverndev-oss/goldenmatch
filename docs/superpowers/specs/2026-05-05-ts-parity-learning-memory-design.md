# Design: TS parity for Learning Memory (goldenmatch-js v0.4.0)

**Date:** 2026-05-05
**Author:** Ben Severn (with Claude)
**Status:** Draft
**Foundation:** [`docs/superpowers/specs/2026-05-04-learning-memory-completion.md`](2026-05-04-learning-memory-completion.md) — Python v1.6.0 spec; this spec ports its semantics to the TS runtime with cross-language storage parity.

## Context

Python `goldenmatch` v1.6.0 shipped Learning Memory end-to-end (PR #71). The TS port (`packages/typescript/goldenmatch`, npm `goldenmatch` v0.3.1) has scaffolding under `src/core/memory/` that predates the Python v1.6.0 work and has drifted: different field names (`verdict` vs `decision`, `feature` vs `matchkey_name`), different hash algorithm (FNV-1a vs SHA-256), no `dataset` scoping, no pipeline integration, no MCP/CLI/REST surfaces, no parity tests.

This spec brings the TS port to **full cross-language parity**: a correction written by Python's `goldenmatch` can be applied identically by `goldenmatch-js`, and vice versa. Same SHA-256 hashes, same SQLite schema, same canonicalization, same trust model, same re-anchor algorithm, same `stale_ambiguous` / `stale_unanchorable` semantics.

## Goals

- TS `Correction` and `LearnedAdjustment` shapes match Python's wire-format (snake_case JSON keys; camelCase TS interface; one translation in `toJSON`/`fromJSON`).
- TS `applyCorrections` produces byte-identical `(adjustedPairs, stats)` JSON output to Python's `apply_corrections` against the same input.
- TS reads a Python-written `.goldenmatch/memory.db` and applies its corrections identically.
- Python reads a TS-written `.goldenmatch/memory.db` and applies its corrections identically.
- Edge-safety preserved: `src/core/` has no `node:*` imports; SQLite-backed store lives in `src/node/`.
- Pipeline integration mirrors Python's hook points; `memoryStats` on `DedupeResult` / `MatchResult` populated; postflight surfaces counts.
- Five MCP tools, CLI subgroup, Python-API mirror, six collection points (BoostTab is skipped — TS TUI has no active-learning tab).
- Parity is locked by tests that run on every CI invocation (JSON canonical form + SQLite `.db` fixture + apply-outcome golden).

## Non-goals

- BoostTab parity (TS TUI has no boost surface).
- Rules layer (Python doesn't have it either; deferred).
- Postgres backend in TS (Python's is also untested in CI; SQLite-only is honest for both runtimes).
- Web review surface (separate effort).
- Cross-runtime concurrent-write WAL guarantees (out of scope; SQLite WAL is the OS's responsibility).
- Backward-compat shims for the existing v0.3.1 `Correction` shape (semver 0.x permits breaking; no value to keeping the old shape working).

## Key decisions

| Decision | Choice | Rationale |
|---|---|---|
| Parity goal | Full cross-language storage parity | A correction written by either runtime applies identically in the other. Higher bar than behavioral parity but unblocks mixed-runtime users; the test cost amortizes. |
| Node SQLite driver | `better-sqlite3` as optional peer dep | Sync API matches package style; same C library as Python sqlite3 (true schema interop); matches existing optional-peer convention (`hnswlib-node`, `@huggingface/transformers`). Throws clear "install better-sqlite3" if missing. |
| Parity verification | JSON canonical form (every test) + committed `.db` fixture (rare schema regen) + apply-outcome golden (every test) | JSON catches hash/field/trust drift cheaply; `.db` fixture catches SQLite schema drift; apply-outcome golden catches algorithmic drift in re-anchor / dual-hash / clamp. |
| Hash algorithm | SHA-256 truncated to 16 hex chars (matches Python `hashlib.sha256(s.encode()).hexdigest()[:16]`) | Cross-language byte-identical requirement. Encoding is UTF-8 on both sides (Python's `str.encode()` default; TS uses `new TextEncoder().encode(s)`). Web Crypto's `crypto.subtle.digest` is available in Node 20+, browsers, and Workers, so it stays edge-safe. Async by necessity. |
| Field naming | snake_case in JSON / SQLite columns; camelCase in TS interface | JSON key parity is required for cross-language; TS code reads idiomatically. One translation point in `toJSON`/`fromJSON`. |
| Source/decision typing | `as const` literal unions (TS StrEnum equivalent) | TS lacks runtime StrEnums; `type CorrectionSource = "steward" \| "boost" \| ...` plus `HIGH_TRUST_SOURCES: ReadonlySet<CorrectionSource>` and `trustForSource()` helper mirrors Python's centralization. |
| Versioning | 0.3.1 → 0.4.0 | Pre-1.0 minor bump is the convention for breaking changes in this package family; npm tag pattern `goldenmatch-js-v0.4.0`. |
| Slicing | Three sequential PRs | Foundation + parity harness, then pipeline + collection points, then surfaces. Mirrors Python's mergeable-phase approach without a single-PR review-fatigue trap. |

## Architecture

Two-tier store, one schema. Edge-safe interface and in-memory implementation in `src/core/memory/`; SQLite-backed implementation in `src/node/memory/`.

```
src/
├─ core/
│  └─ memory/
│     ├─ types.ts          # Correction, LearnedAdjustment, CorrectionStats, MemoryStore interface
│     ├─ store.ts          # InMemoryStore implements MemoryStore
│     ├─ hash.ts           # SHA-256 helpers (Web Crypto)
│     ├─ corrections.ts    # applyCorrections + collision-safe re-anchor
│     └─ learner.ts        # MemoryLearner (threshold tuning)
└─ node/
   └─ memory/
      ├─ sqlite-store.ts        # SqliteMemoryStore implements MemoryStore
      └─ sibling-review-queue.ts # ReviewQueue at <memory_path>.parent / "review_queue.db"
```

Edge runtimes get the in-memory backend for free. Node runtimes opt into SQLite by installing `better-sqlite3` and constructing `SqliteMemoryStore({ path: ".goldenmatch/memory.db" })`. Same `MemoryStore` interface — pipeline code is backend-agnostic.

## Data model

### `Correction`

TS interface (camelCase):

```typescript
interface Correction {
  readonly id: string;                    // UUIDv4
  readonly idA: number;                   // canonical: idA <= idB always
  readonly idB: number;
  readonly decision: Decision;            // "approve" | "reject"
  readonly source: CorrectionSource;      // "steward" | "boost" | "unmerge" | "agent" | "llm" | "api"
  readonly trust: number;                 // 1.0 (human) or 0.5 (agent)
  readonly fieldHash: string;             // SHA-256[:16] of matchkey field values
  readonly recordHash: string;            // "<fullHashA>:<fullHashB>", __row_id__ excluded
  readonly originalScore: number;
  readonly matchkeyName: string | null;
  readonly reason: string | null;
  readonly dataset: string | null;
  readonly createdAt: Date;
}
```

JSON wire format (snake_case to match Python):

```json
{
  "id": "<uuid>",
  "id_a": 1,
  "id_b": 2,
  "decision": "reject",
  "source": "steward",
  "trust": 1.0,
  "field_hash": "<16-hex>",
  "record_hash": "<16-hex>:<16-hex>",
  "original_score": 0.92,
  "matchkey_name": "identity",
  "reason": null,
  "dataset": "customers",
  "created_at": "2026-05-04T12:00:00Z"
}
```

ISO-8601 UTC timestamps so timezone interpretation cannot drift across runtimes.

### `CorrectionStats`

```typescript
interface CorrectionStats {
  readonly applied: number;
  readonly stale: number;
  readonly staleAmbiguous: number;
  readonly staleUnanchorable: number;
  readonly stalePairs: ReadonlyArray<readonly [number, number]>;
  readonly totalPairs: number;
  readonly failed?: boolean;
  readonly error?: string;
}
```

`failed`/`error` populated when `applyCorrections` itself crashed; `_applyMemoryPost` returns this sentinel rather than `null` so postflight can surface "Memory: FAILED — see logs."

### `LearnedAdjustment`

```typescript
interface LearnedAdjustment {
  readonly matchkeyName: string;
  readonly threshold: number | null;
  readonly fieldWeights: Record<string, number> | null;  // stubbed; always null in v0.4.0
  readonly sampleSize: number;
  readonly learnedAt: Date;
}
```

### `MemoryConfig`

```typescript
interface MemoryConfig {
  readonly enabled: boolean;
  readonly backend: "memory" | "sqlite";
  readonly path?: string;
  readonly dataset?: string;            // null/undefined → pipeline derives default
  readonly reanchor?: boolean;          // default true
  readonly trust?: { human: number; agent: number };
  readonly learning?: { thresholdMinCorrections: number; weightsMinCorrections: number };
}
```

Validator rejects empty/whitespace `dataset` (mirrors Python's Pydantic field validator).

### `MemoryStore` interface

```typescript
interface MemoryStore {
  addCorrection(c: Correction): Promise<void>;
  getCorrection(idA: number, idB: number, dataset: string | null): Promise<Correction | null>;
  getCorrections(opts?: { dataset?: string | null }): Promise<Correction[]>;
  countCorrections(dataset?: string | null): Promise<number>;
  correctionsSince(since: Date): Promise<Correction[]>;
  saveAdjustment(a: LearnedAdjustment): Promise<void>;
  getAdjustment(matchkeyName: string): Promise<LearnedAdjustment | null>;
  getAllAdjustments(): Promise<LearnedAdjustment[]>;
  lastLearnTime(): Promise<Date | null>;
  close?(): Promise<void>;
}
```

All methods async. The hash module is unavoidably async (Web Crypto). Wrapping `better-sqlite3`'s sync API behind async methods is deliberate: the interface convergence keeps `InMemoryStore` (which calls async `computeRecordHash` during apply) and `SqliteMemoryStore` (sync internally) interchangeable to callers. Pipeline code awaits both. The async wrappers are zero-cost (`Promise.resolve(syncResult)`); the alternative — a split sync/async interface — would push the await/non-await branch into every caller. Symmetric to Python semantically; parity tests assert outcomes, not call shapes.

### SQLite schema (parity-locked with Python)

```sql
CREATE TABLE IF NOT EXISTS corrections (
    id TEXT PRIMARY KEY,
    id_a INTEGER, id_b INTEGER,
    decision TEXT, source TEXT, trust REAL,
    field_hash TEXT, record_hash TEXT,
    original_score REAL,
    matchkey_name TEXT,
    reason TEXT, dataset TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(id_a, id_b, dataset)
);
CREATE INDEX IF NOT EXISTS idx_corrections_pair ON corrections(id_a, id_b, dataset);

CREATE TABLE IF NOT EXISTS adjustments (
    matchkey_name TEXT PRIMARY KEY,
    threshold REAL, field_weights TEXT,
    sample_size INTEGER,
    learned_at TIMESTAMP
);
```

Byte-identical to `goldenmatch/core/memory/store.py::_SCHEMA`. Both runtimes' SQLite drivers use the same C library, so DDL output is identical.

## Components

### `src/core/memory/types.ts` (new, ~150 LOC)

Interface declarations only. `Correction`, `LearnedAdjustment`, `CorrectionStats`, `MemoryConfig`, `MemoryStore`, the literal-union types, `HIGH_TRUST_SOURCES`, `trustForSource(s: CorrectionSource): number`, the JSON `<-> Correction` translators.

### `src/core/memory/hash.ts` (new, ~80 LOC)

Hash input format mirrors Python's `core/memory/corrections.py` exactly. **Values only, no key-name interpolation** — the column names are NOT included in the hashed string. This is the load-bearing parity invariant; `<col>=<val>` formatting would silently break cross-language identity.

- `sha256_16(s: string): Promise<string>` — `bytesToHex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s))).slice(0, 16)`. UTF-8 encoding is mandatory (matches Python's `str.encode()` default).
- `computeFieldHash(rowAVals, rowBVals): Promise<string>` — `sha256_16(rowAVals.concat(rowBVals).map(String).join("|"))`. Mirrors Python's `compute_field_hash`: `"|".join(str(v) for v in row_a_vals + row_b_vals)`.
- `computeRecordHash(row, columns): Promise<string>` — sort the column names, exclude `__row_id__`, project values in sorted-column order, stringify, join with `"|"`. Mirrors Python's `compute_record_hash`: `"|".join(str(v) for v in row)` after `df.select(sorted(content_cols)).row(0)`.
- `computeRecordHashes(rows, columns): Promise<Map<rowId, hash>>` — async batch helper for the re-anchor path. Internal: builds the same per-row stringification then calls `sha256_16` in parallel via `Promise.all`. Mirrors Python's `_build_hash_to_rids` semantics; the implementation is row-loop rather than vectorized polars (no polars in TS), but the output is byte-identical for the same input.

Hash agreement with Python is asserted by the JSON parity test (Section 4) using fixture inputs and known SHA-256 outputs. The fixture suite includes at least one `__row_id__`-exclusion case: two rows with identical content fields but different `__row_id__` values must produce the same `record_hash`.

### `src/core/memory/store.ts` (rewrite, ~200 LOC)

`InMemoryStore implements MemoryStore`. `Map<string, Correction>` keyed on `<canonA>|<canonB>|<dataset>` enforces `UNIQUE(id_a, id_b, dataset)`. Trust upsert: incoming correction with `trust < existing.trust` ignored; same-tier overwrites (latest wins). Same as Python.

JSON `toJSON()` / `fromJSON(json)` for parity tests.

### `src/core/memory/corrections.ts` (rewrite, ~250 LOC)

`applyCorrections(scoredPairs, store, df, matchkeyFields, opts)`. Algorithm mirrors Python's spec Addition 1:

1. Single fetch via `store.getCorrections({ dataset })`. Empty store → return `[scoredPairs, emptyStats]`.
2. Build `hashToRids: Map<string, number[]>` via `computeRecordHashes(df.rows, sortedNonInternalCols)` — one async batch.
3. For each correction: prefer direct row-id match (`idA in currentRids && idB in currentRids`). Else if `reanchor`, look up `recordHashA` and `recordHashB` in `hashToRids`; re-anchor only when both sides resolve uniquely (`length === 1`). Ambiguous → `staleAmbiguous += 1`. No match either side → `staleUnanchorable += 1`.
4. Apply with existing dual-hash safety: `currentFieldHash === c.fieldHash && currentRecordHash === c.recordHash` (or both empty — empty-hash short-circuit). Match → clamp score to 1.0/0.0; mismatch → keep original score, `stale += 1`.

`reanchor: false` opts out of step 3; corrections whose row IDs are gone become `staleUnanchorable`.

### `src/core/memory/learner.ts` (rewrite, ~220 LOC)

`MemoryLearner` with `hasNewCorrections()` and `learn(matchkeyName?)`. Threshold tuning at 10+ corrections via grid search weighted by trust — same algorithm as Python's `_compute_threshold`. Field weights stubbed (returns null) — same as Python.

### `src/node/memory/sqlite-store.ts` (new, ~250 LOC)

`SqliteMemoryStore implements MemoryStore`. Loads `better-sqlite3` via `await import("better-sqlite3" as string)` (matches package's optional-peer convention; the `as string` cast prevents tsup from resolving at build time). Throws `"better-sqlite3 is required for SqliteMemoryStore. Install it: npm install better-sqlite3"` if missing.

Identical DDL to Python. CRUD methods mirror `MemoryStore.add_correction`, `get_corrections`, etc. Trust upsert via `DELETE + INSERT` in a `BEGIN/COMMIT` transaction (same as Python). Async wrapper around the sync `better-sqlite3` API so the `MemoryStore` interface stays consistent.

### `src/node/memory/sibling-review-queue.ts` (new, ~100 LOC)

Wraps the existing `SQLiteReviewQueueBackend` (or adds a thin shim if needed) opening at `path.dirname(memoryPath) + "/review_queue.db"`. Mirrors Python's `_enqueue_stale_pairs`.

## Pipeline integration

In `src/core/pipeline.ts`, two helpers added near the top:

```typescript
async function _applyMemoryPre(
  config: GoldenMatchConfig,
  matchkeys: MatchkeyConfig[],  // mutated in place
  store: MemoryStore | null,
): Promise<void> {
  if (!store) return;
  const learner = new MemoryLearner(store, config.memory.learning);
  if (!(await learner.hasNewCorrections())) return;
  const adjustments = await learner.learn();
  for (const adj of adjustments) {
    if (adj.threshold == null) continue;
    for (const mk of matchkeys) {
      if (mk.threshold == null) continue;
      if (!adj.matchkeyName || adj.matchkeyName === mk.name || adj.matchkeyName === "_default") {
        mk.threshold = adj.threshold;  // in-place mutation per Python's contract
      }
    }
  }
}

async function _applyMemoryPost(
  config: GoldenMatchConfig,
  scoredPairs: ScoredPair[],
  df: DataFrame,
  matchkeyFields: string[],
  store: MemoryStore | null,
): Promise<[ScoredPair[], CorrectionStats | null]> {
  if (!store) return [scoredPairs, null];
  try {
    return await applyCorrections(scoredPairs, store, df, matchkeyFields, {
      dataset: config.memory.dataset ?? null,
      reanchor: config.memory.reanchor ?? true,
    });
  } catch (e) {
    logger.warning("Memory apply_corrections failed: %s", String(e));
    return [scoredPairs, { ...emptyStats(scoredPairs.length), failed: true, error: String(e) }];
  }
}
```

Called from `_runDedupePipeline` and `_runMatchPipeline`. Same insertion strategy as Python's `pipeline.py:497` (between scoring and postflight). Stale-pair enqueue follows the same shape as Python.

`MemoryStore` lifecycle: caller opens it (in `dedupeFile` / `dedupeDf` from the config), pipeline borrows the handle. `using` declaration (TS 5.2+) for cleanup, fallback `try/finally` if target is below 5.2.

## Postflight rendering

In `src/core/autoconfigVerify.ts` (the TS counterpart to `core/autoconfig_verify.py`), `PostflightReport.toString()` (or its renderer) appends a single line when `memoryStats` is set and any counter is non-zero:

```
Memory: 12 corrections applied, 0 stale, 0 stale-ambiguous, 0 unanchorable
```

When `memoryStats.failed` is true:

```
Memory: FAILED -- see logs
```

ASCII only. Omit the line entirely when `memoryStats == null` or all counters are zero.

## Collection points

Every surface gains an optional `memoryStore?: MemoryStore` parameter. When provided, the surface writes a `Correction` after its existing logic. Backward-compatible — pre-v0.4.0 callers that don't pass `memoryStore` see no behavior change.

| Surface | File | Source | Trust | Hash collection |
|---|---|---|---|---|
| ReviewQueue | `core/review-queue.ts::approve/reject` (base class only) | `"steward"` | 1.0 | full hashes from df + matchkeyFields |
| Unmerge record | `core/cluster.ts::unmergeRecord` | `"unmerge"` | 1.0 | empty hashes (no df in scope) |
| Unmerge cluster | `core/cluster.ts::unmergeCluster` | `"unmerge"` | 1.0 | empty hashes |
| LLM scorer | `core/llm/score-pairs.ts` (or wherever the scoring entry point is) | `"llm"` | 0.5 | full hashes from df |
| Agent MCP tool | `node/mcp/agent-tools.ts::agent_approve_reject` (if exists; else skip) | `"agent"` | 0.5 | full hashes via session df |
| REST decide | `node/api/server.ts::POST /reviews/decide` | `"steward"` | 1.0 | empty hashes (REST has no df in scope) |
| Python API mirror | `core/api.ts::addCorrection` | `"api"` | 0.5 | empty hashes |

BoostTab is skipped — TS TUI has no active-learning tab.

Trust mapping is centralized: every surface calls `trustForSource(source)` rather than inlining the `if source in {...}` check.

## Surfaces

### Python API mirror (`src/core/api.ts`)

```typescript
export async function getMemory(opts?: { path?: string; backend?: "memory" | "sqlite" }): Promise<MemoryStore>;
export async function addCorrection(args: {
  idA: number; idB: number; decision: Decision;
  source?: CorrectionSource;        // default "api" → trust 0.5
  reason?: string; dataset?: string; matchkeyName?: string;
  path?: string;
}): Promise<void>;
export async function learn(args?: { matchkeyName?: string; path?: string }): Promise<LearnedAdjustment[]>;
export async function memoryStats(args?: { path?: string }): Promise<{ count: number; lastLearnTime: Date | null; adjustments: LearnedAdjustment[] }>;
```

Re-exported from `src/index.ts`. Trust mapping: `"steward"`/`"boost"`/`"unmerge"` → 1.0; everything else → 0.5. `"api"` defaults to 0.5; users pass `source: "steward"` for human trust.

### CLI subgroup (`src/cli.ts`)

Commander subcommand `goldenmatch-js memory <stats|learn|export|import|show>`. Same flag shapes as Python:

- `goldenmatch-js memory stats [--path <path>]`
- `goldenmatch-js memory learn [--matchkey-name <name>] [--path <path>]`
- `goldenmatch-js memory export <out> [--path <path>]`
- `goldenmatch-js memory import <src> [--path <path>]`
- `goldenmatch-js memory show <idA> <idB> [--path <path>]`

CSV import skips malformed rows with a warning (matches Python's lenient default).

### MCP tools (`src/node/mcp/memory-tools.ts` new, ~300 LOC)

Five tools, identical input schemas to Python's `mcp/memory_tools.py`:

- `list_corrections`
- `add_correction` (`source: "agent"`, `trust: 0.5`)
- `learn_thresholds`
- `memory_stats`
- `memory_export`

Module exports: `MEMORY_TOOLS: Tool[]`, `MEMORY_TOOL_NAMES: ReadonlySet<string>`, `handleMemoryTool(name, arguments): Promise<TextContent[]>`. Each handler instantiates its own `MemoryStore` (no shared global state), traps SQLite errors and returns structured TextContent rather than crashing the MCP session.

`server.ts` (current header at line 6 reads `~20 tools`) imports and registers `MEMORY_TOOLS` and merges `MEMORY_TOOL_NAMES` into the dispatch chain. PR 3 acceptance criteria includes:
1. Count the entries in the post-merge `TOOLS` array.
2. Update the description literal at `server.ts:6` to the exact post-merge count (e.g. `Exposes 25 tools covering ...`).
3. Add `test_memory_tools_registered` asserting the count matches the description string via regex (`/Exposes (\d+) tools/`).

The current TS server uses a flat `TOOLS: readonly Tool[]` array (not Python's modular registration). PR 3 keeps that shape — `TOOLS = [...EXISTING, ...MEMORY_TOOLS]` — rather than refactoring to a registration model.

### Explainer integration

`ReviewItem` gains a `why?: string` field populated by `whyForCorrection(correction, df, matchkeyFields, { useLlm? })`. Default is the deterministic template (mirror of Python's `explain_pair_nl`); LLM upgrade when an API key is set, via a new `llmExplainPair` function in `src/core/llm/`.

## Cross-language parity

Three fixture files committed under `packages/typescript/goldenmatch/tests/parity/fixtures/`:

1. **`memory_corrections.json`** — canonical 12-correction dataset covering each `CorrectionSource`, both `Decision`s, both empty-hash and full-hash collections, a same-tier latest-wins case, an ambiguous-collision case. Generated by `tests/parity/gen_memory_fixtures.py` (Python source-of-truth).

2. **`memory.db`** — SQLite file written by Python from the same JSON. Regenerated when schema changes via `gen_memory_fixtures.py --rebuild-db`. CI fails if the committed `.db` doesn't match a freshly regenerated one byte-for-byte. **Determinism requirement:** the generator MUST seed all `created_at` values from the JSON fixture (which has fixed ISO-8601 strings) — never `datetime.now()`. The SQLite schema's `DEFAULT CURRENT_TIMESTAMP` is bypassed because `add_correction` always inserts the dataclass's `created_at`. The generator must also call `PRAGMA user_version = N` (a fixed integer) and avoid any other source of nondeterminism (e.g., random UUIDs — fixture UUIDs are pinned in the JSON).

3. **`memory_apply_inputs.json`** — frozen input df + scored-pairs list + expected `(adjustedPairs, stats)` JSON output for the apply-outcome golden.

The Python side gets a committed copy at `packages/python/goldenmatch/tests/parity/memory/` (commit-twice rather than symlink — Windows symlink fragility).

### Three parity tests, both runtimes

- **JSON round-trip** (`tests/parity/memory_json.parity.test.ts` + `tests/parity/test_memory_json_parity.py`): load `memory_corrections.json` → build store → dump → assert byte-equal to source. Catches hash drift, field rename completeness, trust mapping divergence.
- **SQLite round-trip** (`memory_sqlite.parity.test.ts` + `test_memory_sqlite_parity.py`): load `memory.db` via `better-sqlite3` (TS) / `sqlite3` (Python) → fetch all corrections → assert each round-trips to the JSON fixture. Catches schema drift.
- **Apply-outcome golden** (`memory_apply.parity.test.ts` + `test_memory_apply_parity.py`): seed store from JSON → run `applyCorrections(scoredPairs, store, df, matchkeyFields)` against `memory_apply_inputs.json` inputs → assert resulting `(adjustedPairs, stats)` matches the JSON-encoded expected output byte-for-byte.

### CI integration

Python parity tests run in the existing `python (goldenmatch)` job; TS parity tests run in the existing `typescript` job. No new CI jobs.

### What this catches

- FNV-1a → SHA-256 migration completeness (any forgotten call site → JSON divergence).
- Field rename completeness (`verdict` → `decision`, `feature` → `matchkey_name`).
- Trust mapping divergence (e.g., if TS forgets to add `"api"` to the high-trust set or vice versa).
- Re-anchor algorithm divergence (collision handling, ambiguous skip, unanchorable counter).
- SQLite schema drift.
- Same-tier latest-wins divergence (timestamp comparison subtleties).

### What this doesn't catch

- Concurrent-write semantics across runtimes (out of scope — SQLite WAL is the OS's job).
- Postflight rendering string parity (cosmetic).
- LLM-explainer prose parity (non-deterministic; only the deterministic fallback is parity-checked).

## Error handling

- Memory layer never blocks the pipeline. Open-failure → `memoryStats=null`, log warning, continue.
- Hash-computation failure at collection time → log warning, write empty hashes (apply-time short-circuits dual-hash check). Do NOT silently swallow.
- `applyCorrections` failure → return `(unchangedPairs, CorrectionStats { failed: true, error })` so postflight surfaces "Memory: FAILED."
- Stale-pair enqueue failure → log warning, continue. Stats still attach to the result.
- SQLite `database is locked` → trapped at the store layer, returned as structured error to MCP callers; logged as warning to pipeline callers.
- `better-sqlite3` not installed → `SqliteMemoryStore` constructor throws with install instructions (NOT a silent fallback to in-memory — that would surprise users who expected persistence).

## Testing

- **Unit tests** (`tests/unit/memory.test.ts` rewritten, ~15 tests) — store CRUD, trust upsert, canonical pair ordering, learner threshold logic.
- **Re-anchor tests** (`tests/unit/memory-reanchor.test.ts` new, ~10 tests) — port the 8 cases from Python's `test_memory_reanchor.py`: row reorder, ambiguous duplicate, edit on matchkey field, edit on non-matchkey, empty store, missing `__row_id__`, unanchorable correction, `reanchor=false` opt-out.
- **Pipeline tests** (`tests/unit/memory-pipeline.test.ts` new, ~5 tests) — applies seeded correction, no-stats-when-disabled, doesn't-open-store-when-disabled, persists-stale-pairs, survives-corrupt-db.
- **E2E tests** (`tests/integration/memory-e2e.test.ts` new, ~7 tests) — mirror Python's 8 scenarios from `test_memory_e2e.py`, skipping the BoostTab one.
- **Parity tests** (`tests/parity/memory_*.parity.test.ts`, 3 files) — JSON + SQLite + apply-outcome from above.
- **Python-side parity tests** (`packages/python/goldenmatch/tests/parity/test_memory_*.py`, 3 files) — same three checks against the same fixtures.

Vitest default timeout 5s except for SHA-256 batch tests (15s — Web Crypto async overhead matters at scale on shared CI runners).

Coverage target: roughly mirror Python's ~70 new tests / 3,500 LOC of production change.

## Slicing

Three sequential PRs:

1. **Foundation rewrite + parity harness.** New types, hash module, in-memory store rewrite, `applyCorrections` rewrite, **learner rewrite (`learner.ts:98-171` references the old `verdict`/`feature` shape and must be migrated alongside the field rename)**, SQLite-backed store, parity fixtures generator + the three parity tests on both sides. Rewrites the existing 7 unit tests. **Breaking change:** `src/core/index.ts:303-304` re-exports `MemoryStore` / `Correction` / `MemoryStoreConfig` — every external consumer of these types breaks at v0.4.0. CHANGELOG must call this out under `[0.4.0] Breaking`. **No pipeline integration yet.** Mergeable on its own.
2. **Pipeline + postflight + collection points.** `_applyMemoryPre`/`_applyMemoryPost`, `memoryStats` on result types, postflight memory line, sibling review queue, six collection points. E2E tests added.
3. **Surfaces.** Five MCP tools, CLI subgroup, Python API mirror, explainer integration. Wraps with the v0.4.0 release: bump `package.json`, add CHANGELOG entry under `[0.4.0]`, tag `goldenmatch-js-v0.4.0`.

Each PR squash-merges per SOP. Cumulative diff target ~2,000-2,500 LOC.

## Risks

- **Web Crypto async overhead.** Hashing every row for re-anchor is one `crypto.subtle.digest` per row. On a 100K-row input that's 100K async calls. Mitigation: batch via `Promise.all` over chunks, or use `node:crypto` synchronous API in the Node-only path (lives behind a `getHasher()` factory in `core/memory/hash.ts` — Node implementation overrides for performance, edge implementation uses Web Crypto). Falls back gracefully on edge.
- **SQLite schema regeneration friction.** Every schema change requires regenerating the `memory.db` fixture. Mitigation: `gen_memory_fixtures.py --rebuild-db` is a one-line CI-checkable command; CI fails loudly if a committed `.db` doesn't match a fresh regen.
- **`better-sqlite3` native compile failures on Windows.** Already a known peer-dep risk for the package family. Mitigation: same installation guidance as `hnswlib-node` (works under Visual Studio Build Tools); document in the README's installation section.
- **TS 5.2 `using` declaration availability.** Package's `package.json` declares `"typescript": "^5.4.0"` (≥5.2 satisfied) and `tsconfig.json` targets ES2022, but `lib` lacks `"ESNext.Disposable"`. PR 1 must add `"ESNext.Disposable"` to `compilerOptions.lib` to enable `using`. Fallback `try/finally` works too if you prefer to skip the lib bump.
- **Existing scaffolding test removal pain.** The 7 tests in the current `tests/unit/memory.test.ts` reference the old `verdict`/`feature` shape. Mitigation: rewrite the file in PR 1 alongside the field rename; don't try to migrate.

## Review notes (2026-05-05)

This spec went through one review pass against the actual Python source and TS port code. Findings folded in:

- **Hash format corrected.** Original draft said `<col1>=<v1>|<col2>=<v2>|...`; Python actually hashes values only, joined by `|`, with no key-name interpolation. The `core/memory/hash.ts` section now mirrors Python verbatim.
- **TextEncoder UTF-8** made explicit in the hash-algorithm decision row.
- **MCP tool count** specifics added: PR 3 must update `server.ts:6`'s description literal to the post-merge count and add a registration test.
- **`__row_id__`-exclusion parity test** added as a required fixture case.
- **Determinism clamp** for `gen_memory_fixtures.py --rebuild-db` made explicit (fixed `created_at`, fixed UUIDs, no `datetime.now()`).
- **TS 5.2 `using`** confirmed feasible (`package.json` already at 5.4); `lib` must add `"ESNext.Disposable"`.
- **Async-everywhere** rationale spelled out (interface convergence between in-memory and SQLite stores; zero-cost wrappers).
- **API churn** flagged as breaking-change CHANGELOG line in PR 1.
- **Learner rewrite** scope made explicit (current `learner.ts` references the old shape).

## Open questions resolved

- Parity goal: full cross-language storage (option b in brainstorming).
- SQLite driver: `better-sqlite3` as optional peer (option a in brainstorming).
- Verification strategy: JSON canonical + SQLite fixture + apply-outcome golden (option b in brainstorming).
- Slicing: three sequential PRs, not one big PR, not 8 phases like Python.
- Versioning: 0.3.1 → 0.4.0 (pre-1.0 breaking-change convention).
- Backward compat: none for the old `verdict`/`feature` shape (semver 0.x permits breaking; no value to keeping the old shape working).

## Out of scope (deferred to later specs)

- BoostTab parity (TS TUI has no boost surface).
- Rules layer (Python doesn't have it either; whole-suite deferred).
- Postgres backend in TS.
- Web review surface.
- Cross-runtime concurrent-write WAL guarantees.
