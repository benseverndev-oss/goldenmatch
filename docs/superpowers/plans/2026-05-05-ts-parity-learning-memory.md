# TS Parity for Learning Memory — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `packages/typescript/goldenmatch` (npm `goldenmatch`) to full cross-language storage parity with Python `goldenmatch` v1.6.0's Learning Memory feature, shipping as goldenmatch-js v0.4.0.

**Architecture:** Three sequential PRs. Foundation rewrite + parity harness lands first (no observable behavior change but locks the wire format). Pipeline integration + collection points lands second (observable end-to-end). Surfaces (MCP, CLI, Python-API mirror) + v0.4.0 release lands third.

**Tech Stack:** TypeScript 5.4 (`using` declaration via `ESNext.Disposable` lib), Node 20+, Web Crypto SubtleCrypto for SHA-256, `better-sqlite3` as optional peer dep, vitest, commander.

**Spec:** `docs/superpowers/specs/2026-05-05-ts-parity-learning-memory-design.md` — read it first; this plan does not duplicate the spec's rationale, decisions, or invariants.

**Foundation:** Python implementation under `packages/python/goldenmatch/goldenmatch/core/memory/` (PR #71, shipped as v1.6.0). Treat it as the source of truth for cross-language hash bytes, SQLite schema, and algorithm semantics.

---

## File structure

All paths relative to `packages/typescript/goldenmatch/` unless noted. PR-by-PR breakdown.

### PR 1 (Foundation + parity harness)

**Create:**
- `src/core/memory/types.ts` — `Correction`, `LearnedAdjustment`, `CorrectionStats`, `MemoryConfig`, `MemoryStore` interface, `CorrectionSource`/`Decision` literal unions, `HIGH_TRUST_SOURCES`, `trustForSource()`. JSON `<-> Correction` translators.
- `src/core/memory/hash.ts` — SHA-256 helpers via Web Crypto (UTF-8 via `TextEncoder`); `sha256_16`, `computeFieldHash`, `computeRecordHash`, `computeRecordHashes` batch.
- `src/node/memory/sqlite-store.ts` — `SqliteMemoryStore implements MemoryStore`. Optional-peer dynamic import of `better-sqlite3`.
- `src/node/memory/index.ts` — re-export `SqliteMemoryStore`.
- `tests/parity/fixtures/memory_corrections.json` — 12-correction canonical set (committed).
- `tests/parity/fixtures/memory_apply_inputs.json` — frozen apply-outcome golden inputs/outputs.
- `tests/parity/fixtures/memory.db` — SQLite file written by Python (committed).
- `tests/parity/memory_json.parity.test.ts` — JSON round-trip parity.
- `tests/parity/memory_sqlite.parity.test.ts` — SQLite fixture round-trip.
- `tests/parity/memory_apply.parity.test.ts` — apply-outcome golden.
- `packages/python/goldenmatch/tests/parity/memory/` — committed copy of the three fixture files (commit-twice; spec rationale).
- `packages/python/goldenmatch/tests/parity/memory/test_memory_json_parity.py`
- `packages/python/goldenmatch/tests/parity/memory/test_memory_sqlite_parity.py`
- `packages/python/goldenmatch/tests/parity/memory/test_memory_apply_parity.py`
- `packages/python/goldenmatch/tests/parity/memory/gen_memory_fixtures.py` — generator (Python source-of-truth for the JSON, `.db`, and apply golden).
- `tests/unit/memory-reanchor.test.ts` — port of the 8 cases from Python `test_memory_reanchor.py`.

**Rewrite (breaking changes vs v0.3.1):**
- `src/core/memory/store.ts` — `InMemoryStore implements MemoryStore`, new shape, async API. `Correction.verdict` to `decision`, `Correction.feature` to `matchkey_name`, plus all v1.6.0 fields.
- `src/core/memory/corrections.ts` — `applyCorrections` collision-safe re-anchor algorithm. FNV-1a hashing removed; SHA-256 only.
- `src/core/memory/learner.ts` — port from Python `learner.py` (threshold tuning at 10+ corrections, weighted by trust, field weights stubbed).
- `tests/unit/memory.test.ts` — rewrite all 7 existing tests to the new shapes.

**Modify:**
- `src/core/index.ts:303-304` — re-exports updated for new types.
- `src/node/index.ts` — add `export * from "./memory/index.js"`.
- `package.json` — add `better-sqlite3` to `peerDependencies` and `peerDependenciesMeta` as optional.
- `tsconfig.json` — add `"ESNext.Disposable"` to `compilerOptions.lib` to enable `using`.

### PR 2 (Pipeline + postflight + collection points)

**Create:**
- `src/node/memory/sibling-review-queue.ts` — opens SQLite review queue at `dirname(memoryPath)/review_queue.db`.
- `tests/unit/memory-pipeline.test.ts` — pipeline integration, ~5 tests.
- `tests/integration/memory-e2e.test.ts` — port the 7 applicable scenarios from Python `test_memory_e2e.py` (skip BoostTab).

**Modify:**
- `src/core/pipeline.ts` — add `_applyMemoryPre` / `_applyMemoryPost` helpers; **`runDedupePipeline` and `runMatchPipeline` become async**; insert hooks at scoring/postflight boundary (~line 290).
- `src/core/api.ts:154,172` — both result-builders attach `memoryStats`; `dedupe`/`match` become async.
- `src/core/types.ts` (DedupeResult, MatchResult interfaces) — add `memoryStats: CorrectionStats | null`.
- `src/core/autoconfigVerify.ts` — `PostflightReport` toString appends one-line memory section per spec.
- `src/core/review-queue.ts` — `approve`/`reject` accept optional `memoryStore`, write `Correction`.
- `src/core/cluster.ts::unmergeRecord, unmergeCluster` — accept optional `memoryStore`, write empty-hash corrections.
- `src/core/llm/scorer.ts` (or wherever pair-level LLM scoring lives) — accept optional `memoryStore`, write per-decision corrections.
- `src/node/api/server.ts::POST /reviews/decide` — accept `memoryStore` from server config, write empty-hash correction.
- `src/cli.ts` — `dedupeCommand` / `matchCommand` (both wrap `runDedupePipeline`) become async.

### PR 3 (Surfaces + v0.4.0 release)

**Create:**
- `src/node/mcp/memory-tools.ts` — five MCP tools per spec.
- `tests/unit/memory-tools.test.ts`
- `tests/unit/memory-cli.test.ts`
- `tests/unit/memory-explainer.test.ts`

**Modify:**
- `src/core/api.ts` — add `getMemory`, `addCorrection`, `learn`, `memoryStats` Python-API mirrors.
- `src/index.ts` — re-export the four API functions.
- `src/node/mcp/server.ts:6` — bump description literal `~20 tools` to exact post-merge count. Import + register `MEMORY_TOOLS`.
- `src/cli.ts` — register `memory` subgroup (`stats|learn|export|import|show`).
- `src/core/review-queue.ts` — `ReviewItem` gains `why?: string` field; populate via new `whyForCorrection`.
- `src/core/llm/explain.ts` (new or modify existing) — add `llmExplainPair` for the LLM upgrade path.
- `package.json` — bump `version` from `0.3.1` to `0.4.0`.
- `CHANGELOG.md` — add `[0.4.0]` section with breaking-change call-out.

---

## Common patterns

**Test directory.** All TS tests live under `packages/typescript/goldenmatch/tests/`. Run from `packages/typescript/goldenmatch/`. Vitest config already in place.

**Test commands:**
- Single test: `cd packages/typescript/goldenmatch && npx vitest run tests/unit/<file>.test.ts`
- Phase smoke: `cd packages/typescript/goldenmatch && npx vitest run tests/unit/memory*.test.ts tests/parity/memory*.parity.test.ts`
- Full TS suite: `cd packages/typescript/goldenmatch && npx tsc --noEmit && npx vitest run`
- Python parity tests: `cd packages/python/goldenmatch && pytest tests/parity/memory/ -v`

**Hashing.** Web Crypto in `core/`, also works in Node 20+. **No sync hashing.** This is the architectural pivot from the v0.3.1 FNV-1a sync path.

**Pipeline async migration.** PR 2 turns the pipeline async. This is an additional breaking change beyond the field renames. CHANGELOG must call it out. Public API callers do `result = await dedupe(rows, config)` instead of `result = dedupe(rows, config)`.

**Optional-peer import.** `await import("better-sqlite3" as string)` — the `as string` cast is mandatory (prevents tsup from resolving at build time). Match the existing pattern from `hnswlib-node` etc.

**Branch.** All three PRs land on the existing `feature/ts-parity-learning-memory` branch (spec already committed). Push intermediate state with `GH_TOKEN=$(gh auth token --user benzsevern) git push`.

**Commit message format.** `feat(memory): <description>` for new behavior, `test(memory): <description>` for test-only changes, `refactor(memory): <description>` for the field-rename rewrites.

**ASCII only** in CLI/log strings (Windows cp1252).

---

## PR 1: Foundation rewrite + parity harness

**Why first:** Nothing user-observable changes, but the wire format gets locked. PR 2 + PR 3 build on the new shapes.

### Phase 1.1: Types + helpers (no logic yet)

**Files:**
- Create: `src/core/memory/types.ts`

- [ ] **Step 1.1.1: Write the failing types module test**

`tests/unit/memory.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import {
  HIGH_TRUST_SOURCES,
  trustForSource,
  type CorrectionSource,
} from "../../src/core/memory/types.js";

describe("Correction source/decision types", () => {
  it("HIGH_TRUST_SOURCES contains exactly steward/boost/unmerge", () => {
    expect(HIGH_TRUST_SOURCES.size).toBe(3);
    expect(HIGH_TRUST_SOURCES.has("steward")).toBe(true);
    expect(HIGH_TRUST_SOURCES.has("boost")).toBe(true);
    expect(HIGH_TRUST_SOURCES.has("unmerge")).toBe(true);
  });

  it("trustForSource maps high-trust to 1.0 and others to 0.5", () => {
    expect(trustForSource("steward")).toBe(1.0);
    expect(trustForSource("agent")).toBe(0.5);
    expect(trustForSource("api")).toBe(0.5);
  });
});
```

- [ ] **Step 1.1.2: Run; confirm fails (module missing)**

`npx vitest run tests/unit/memory.test.ts`. Expected failure: cannot find module `types.js`.

- [ ] **Step 1.1.3: Implement `src/core/memory/types.ts`**

Full type definitions: `CorrectionSource`/`Decision` literal unions, `HIGH_TRUST_SOURCES` `ReadonlySet`, `trustForSource()`, `Correction` interface (camelCase fields per spec), `LearnedAdjustment`, `CorrectionStats` (with `applied`, `stale`, `staleAmbiguous`, `staleUnanchorable`, `stalePairs`, `totalPairs`, optional `failed`/`error`), `MemoryConfig`, `MemoryStore` async interface (10 methods per spec), `CorrectionJSON` snake_case wire format, `correctionToJSON` / `correctionFromJSON` translators with ISO-8601 UTC timestamps.

Verbatim shape per spec section "Data model"; do NOT improvise field names.

- [ ] **Step 1.1.4: Run; confirm passes**

- [ ] **Step 1.1.5: Add JSON round-trip test**

```typescript
import { correctionToJSON, correctionFromJSON, type Correction } from "../../src/core/memory/types.js";

it("Correction JSON round-trip is identity", () => {
  const c: Correction = {
    id: "abc-123", idA: 5, idB: 7, decision: "reject", source: "steward",
    trust: 1.0, fieldHash: "abc123", recordHash: "abc123:def456",
    originalScore: 0.92, matchkeyName: "identity", reason: null,
    dataset: "customers", createdAt: new Date("2026-05-04T12:00:00.000Z"),
  };
  const r = correctionFromJSON(correctionToJSON(c));
  expect(r).toEqual(c);
});
```

Run; confirm passes.

- [ ] **Step 1.1.6: Commit**

```bash
git add packages/typescript/goldenmatch/src/core/memory/types.ts \
        packages/typescript/goldenmatch/tests/unit/memory.test.ts
git commit -m "refactor(memory): types module with StrEnum-equivalent unions and JSON translators"
```

### Phase 1.2: Hash module

**Files:**
- Create: `src/core/memory/hash.ts`

**Critical parity invariant:** values only, joined by `|`, no `<col>=<val>` formatting. Verify by hand against `core/memory/corrections.py:68-86`.

- [ ] **Step 1.2.1: Pin Python hash byte values to lock parity at the bit level**

Run from a shell:
```bash
python -c "import hashlib; \
  print('hello:', hashlib.sha256(b'hello').hexdigest()[:16]); \
  print('cafe:', hashlib.sha256('café'.encode()).hexdigest()[:16]); \
  print('a|1|b|2:', hashlib.sha256(b'a|1|b|2').hexdigest()[:16]); \
  print('Acme|10001:', hashlib.sha256(b'Acme|10001').hexdigest()[:16])"
```

Record the outputs in a comment at the top of `tests/unit/memory-hash.test.ts`. These four values lock the cross-language byte parity.

- [ ] **Step 1.2.2: Failing hash tests** (`tests/unit/memory-hash.test.ts`)

```typescript
import { describe, it, expect } from "vitest";
import { sha256_16, computeFieldHash, computeRecordHash } from "../../src/core/memory/hash.js";

describe("hash byte parity with Python", () => {
  it("sha256_16 matches Python pinned values", async () => {
    expect(await sha256_16("hello")).toBe("<paste from step 1.2.1>");
    expect(await sha256_16("café")).toBe("<paste from step 1.2.1>");
  });

  it("computeFieldHash uses values only joined with |", async () => {
    expect(await computeFieldHash(["a", "1"], ["b", "2"])).toBe("<from step 1.2.1>");
  });

  it("computeRecordHash excludes __row_id__ and sorts columns", async () => {
    const row = { name: "Acme", zip: "10001", __row_id__: 42 };
    // Sorted non-internal cols: [name, zip]; values: ["Acme", "10001"]; joined: "Acme|10001"
    expect(await computeRecordHash(row, Object.keys(row))).toBe("<from step 1.2.1>");
  });

  it("same content / different __row_id__ produces same hash", async () => {
    const r1 = { name: "Acme", zip: "10001", __row_id__: 42 };
    const r2 = { name: "Acme", zip: "10001", __row_id__: 99 };
    expect(await computeRecordHash(r1, Object.keys(r1)))
      .toBe(await computeRecordHash(r2, Object.keys(r2)));
  });
});
```

- [ ] **Step 1.2.3: Confirm fails**

- [ ] **Step 1.2.4: Implement `src/core/memory/hash.ts`**

```typescript
function bytesToHex(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let hex = "";
  for (let i = 0; i < bytes.length; i++) {
    hex += bytes[i].toString(16).padStart(2, "0");
  }
  return hex;
}

export async function sha256_16(s: string): Promise<string> {
  const data = new TextEncoder().encode(s);
  const buf = await crypto.subtle.digest("SHA-256", data);
  return bytesToHex(buf).slice(0, 16);
}

export async function computeFieldHash(
  rowAVals: ReadonlyArray<unknown>,
  rowBVals: ReadonlyArray<unknown>,
): Promise<string> {
  return sha256_16([...rowAVals, ...rowBVals].map(String).join("|"));
}

export async function computeRecordHash(
  row: Record<string, unknown>,
  columns: ReadonlyArray<string>,
): Promise<string> {
  const cols = [...columns].filter((c) => c !== "__row_id__").sort();
  return sha256_16(cols.map((c) => String(row[c])).join("|"));
}

export async function computeRecordHashes(
  rows: ReadonlyArray<Record<string, unknown>>,
  columns: ReadonlyArray<string>,
): Promise<Map<number, string>> {
  const cols = [...columns].filter((c) => c !== "__row_id__").sort();
  const out = new Map<number, string>();
  const promises = rows.map(async (row) => {
    const rid = row["__row_id__"] as number;
    const hash = await sha256_16(cols.map((c) => String(row[c])).join("|"));
    return [rid, hash] as const;
  });
  for (const [rid, hash] of await Promise.all(promises)) {
    out.set(rid, hash);
  }
  return out;
}
```

- [ ] **Step 1.2.5: Run; confirm passes**

- [ ] **Step 1.2.6: Commit**

```bash
git commit -m "feat(memory): SHA-256 hash module with cross-language byte parity"
```

### Phase 1.3: InMemoryStore rewrite

**Files:**
- Modify: `src/core/memory/store.ts` (full rewrite)

- [ ] **Step 1.3.1: Failing tests for `InMemoryStore` CRUD + trust upsert**

Cover: round-trip with canonicalization, trust upsert (lower trust ignored, same-tier latest wins), dataset scoping. Reference Python tests in `test_memory_store.py` for canonical assertions.

- [ ] **Step 1.3.2: Rewrite `src/core/memory/store.ts`**

`InMemoryStore implements MemoryStore`. `Map<string, Correction>` keyed on `<canonA>|<canonB>|<dataset>` enforces uniqueness. Trust upsert: incoming with `trust < existing.trust` ignored; same-tier overwrites. Canonicalize `(idA, idB)` to `(min, max)` on insert. Reference Python `core/memory/store.py:70-249` line-by-line.

All methods async per the spec interface.

- [ ] **Step 1.3.3: Run; confirm passes**

- [ ] **Step 1.3.4: Verify the v0.3.1 `verdict`/`feature` shape is gone**

`grep -rn "verdict\|\.feature" packages/typescript/goldenmatch/src packages/typescript/goldenmatch/tests` should return nothing in memory-related files.

- [ ] **Step 1.3.5: Commit**

```bash
git commit -m "refactor(memory): InMemoryStore with canonical-pair upsert and dataset scoping (BREAKING)"
```

### Phase 1.4: applyCorrections rewrite

**Files:**
- Modify: `src/core/memory/corrections.ts` (full rewrite)
- Create: `tests/unit/memory-reanchor.test.ts` (port of 8 Python tests)

**Algorithm reference:** spec section "Components -> src/core/memory/corrections.ts" + Python `core/memory/corrections.py:80-200`.

- [ ] **Step 1.4.1: Port the 8 reanchor test cases from Python**

`tests/unit/memory-reanchor.test.ts`. Each test mirrors a case from `packages/python/goldenmatch/tests/test_memory_reanchor.py`:

1. row reorder preserves correction
2. ambiguous duplicate rows: refuse to re-anchor (`staleAmbiguous` += 1)
3. edit on matchkey field marks stale
4. edit on non-matchkey field still marks stale (record_hash captures all)
5. `reanchor=false` skips re-anchor pass
6. empty store returns scored pairs unchanged
7. missing `__row_id__` column returns input unchanged with warning
8. unanchorable correction (no recordHash, row IDs gone) counted in `staleUnanchorable`

Each test uses a small helper `seedReject(store, df, idA, idB)` that computes the field/record hashes and inserts.

- [ ] **Step 1.4.2: Confirm fails (corrections.ts is the v0.3.1 shape)**

- [ ] **Step 1.4.3: Rewrite `src/core/memory/corrections.ts`**

Translate Python `apply_corrections` line-by-line:

1. Single fetch via `store.getCorrections({ dataset })`. Empty store -> return `[scoredPairs, emptyStats]`.
2. Build `recordHash -> [rowIds]` map via `computeRecordHashes(df, cols)` -> invert.
3. For each correction: prefer direct row-id match (`currentRids.has(idA) && currentRids.has(idB)`). Else if `reanchor`, look up `recordHashA` and `recordHashB`; re-anchor only when both sides resolve uniquely. Ambiguous -> `staleAmbiguous += 1`. No match either side -> `staleUnanchorable += 1`. `reanchor=false` -> `staleUnanchorable += 1`.
4. Apply with dual-hash safety: `currFh === c.fieldHash && currRh === c.recordHash`. Empty hashes short-circuit (always apply when row IDs match).

The algorithm signature is async because hash module is async:
```typescript
export async function applyCorrections(
  scoredPairs: ReadonlyArray<ScoredPair>,
  store: MemoryStore,
  df: ReadonlyArray<Row>,
  matchkeyFields: ReadonlyArray<string>,
  opts: { dataset?: string | null; reanchor?: boolean } = {},
): Promise<readonly [ScoredPair[], CorrectionStats]>
```

- [ ] **Step 1.4.4: Run reanchor tests; confirm all 8 pass**

- [ ] **Step 1.4.5: Commit**

```bash
git commit -m "feat(memory): applyCorrections with collision-safe vectorized re-anchor"
```

### Phase 1.5: MemoryLearner rewrite

**Files:**
- Modify: `src/core/memory/learner.ts` (full rewrite)

**Reference:** Python `core/memory/learner.py:1-118`. Threshold tuning at 10+ corrections via grid search weighted by trust. Field weights stub returns null.

- [ ] **Step 1.5.1: Failing learner tests**

Cover: `hasNewCorrections()` reflects last learn time; `learn()` returns empty when fewer than 10 corrections; `learn()` computes threshold via weighted grid search at 10+ corrections (6 approves at 0.85, 6 rejects at 0.55 -> threshold lands between); field weights remain null.

- [ ] **Step 1.5.2: Confirm fails**

- [ ] **Step 1.5.3: Implement `src/core/memory/learner.ts`**

Translate Python `learner.py` line-by-line. Same `_compute_threshold` grid-search semantics; same trust-weighted misclassification cost. Async methods.

- [ ] **Step 1.5.4: Run; confirm passes**

- [ ] **Step 1.5.5: Commit**

```bash
git commit -m "refactor(memory): MemoryLearner port (threshold tuning, field weights stubbed)"
```

### Phase 1.6: SqliteMemoryStore

**Files:**
- Create: `src/node/memory/sqlite-store.ts`
- Create: `src/node/memory/index.ts`
- Modify: `package.json`
- Modify: `src/node/index.ts`

- [ ] **Step 1.6.1: Add `better-sqlite3` to package.json**

```json
"peerDependencies": {
  "better-sqlite3": "^11.0.0"
},
"peerDependenciesMeta": {
  "better-sqlite3": { "optional": true }
}
```

Also add `"better-sqlite3": "^11.0.0"` to `devDependencies` so tests run locally.

- [ ] **Step 1.6.2: Failing tests for SqliteMemoryStore**

Cover: schema initialization, addCorrection round-trip, trust upsert via DELETE+INSERT in a transaction, dataset scoping, schema column names match Python (`id_a`, `id_b`, etc.), `UNIQUE(id_a, id_b, dataset)` constraint enforced, clear error message when `better-sqlite3` is not installed.

- [ ] **Step 1.6.3: Implement `src/node/memory/sqlite-store.ts`**

```typescript
import type { MemoryStore, Correction, LearnedAdjustment, MemoryConfig } from "../../core/memory/types.js";

const SCHEMA = `
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
`;

export class SqliteMemoryStore implements MemoryStore {
  private db: any = null;
  constructor(private readonly config: MemoryConfig & { path: string }) {}

  async init(): Promise<void> {
    let mod: any;
    try {
      mod = await import("better-sqlite3" as string);
    } catch {
      throw new Error(
        "better-sqlite3 is required for SqliteMemoryStore. Install it: npm install better-sqlite3",
      );
    }
    const BetterSqlite3 = mod.default ?? mod;
    this.db = new BetterSqlite3(this.config.path);
    // run multi-statement DDL once
    this.db.prepare(SCHEMA).run; // see implementer note below
  }

  // ... addCorrection, getCorrection, getCorrections, etc.
  async close(): Promise<void> { this.db?.close(); this.db = null; }
}
```

**Implementer note:** `better-sqlite3`'s database object has methods for running raw multi-statement SQL (consult the library's docs for the canonical multi-statement runner). Use that to apply `SCHEMA`. Do not split into individual `prepare(...).run()` calls unless required.

For the trust-upsert transaction, use `better-sqlite3`'s `db.transaction(fn)` API to wrap a `DELETE` + `INSERT` into one atomic block. Mirror Python `store.py:113-132` exactly.

Port each method from Python `store.py:100-225`. Field-name mapping: TS `idA` -> SQL `id_a`, TS `matchkeyName` -> SQL `matchkey_name`, TS `createdAt` (Date) -> SQL `created_at` (ISO string). Read on output, hydrate the `Date`. Use parameterized SQL throughout.

- [ ] **Step 1.6.4: Run; confirm passes**

- [ ] **Step 1.6.5: `src/node/memory/index.ts` re-exports**

```typescript
export { SqliteMemoryStore } from "./sqlite-store.js";
```

- [ ] **Step 1.6.6: `src/node/index.ts` adds the memory export**

```typescript
export * from "./memory/index.js";
```

- [ ] **Step 1.6.7: Commit**

```bash
git commit -m "feat(memory): SqliteMemoryStore via better-sqlite3 optional peer dep"
```

### Phase 1.7: Parity fixtures + tests

**Files:**
- Create: `packages/python/goldenmatch/tests/parity/memory/gen_memory_fixtures.py`
- Create: `packages/python/goldenmatch/tests/parity/memory/__init__.py`
- Create: 3 fixture files (committed) on both sides
- Create: 3 TS parity tests + 3 Python parity tests

- [ ] **Step 1.7.1: Write `gen_memory_fixtures.py`**

Pinned UUIDs and timestamps; no `datetime.now()`, no random UUIDs.

```python
"""Generate parity fixtures for cross-language Learning Memory tests.

Determinism: every value is fixed. No datetime.now(), no random UUIDs.
Run with --rebuild-db to regenerate the SQLite fixture.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from goldenmatch.core.memory.store import MemoryStore, Correction

FIXTURE_DIR = Path(__file__).parent / "fixtures"

def make_corrections() -> list[Correction]:
    base_ts = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
    # 12 corrections: each source x both decisions, plus an ambiguous case,
    # plus an empty-hash case, plus a same-tier latest-wins case
    return [
        # ... pinned UUIDs and field values
    ]

def write_json(corrections):
    out = [c.to_dict() for c in corrections]
    (FIXTURE_DIR / "memory_corrections.json").write_text(json.dumps(out, indent=2))

def write_db(corrections):
    db_path = FIXTURE_DIR / "memory.db"
    db_path.unlink(missing_ok=True)
    store = MemoryStore(backend="sqlite", path=str(db_path))
    for c in corrections:
        store.add_correction(c)
    store.close()

def write_apply_inputs():
    # Frozen df (rows + matchkeyFields) + scored-pairs + expected (adjustedPairs, stats)
    pass

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild-db", action="store_true")
    args = ap.parse_args()
    corrections = make_corrections()
    write_json(corrections)
    write_apply_inputs()
    if args.rebuild_db:
        write_db(corrections)
    print(f"Fixtures written to {FIXTURE_DIR}")
```

If `Correction.to_dict()` doesn't exist, add it to the Python dataclass for parity output (snake_case keys, ISO-8601 UTC timestamp).

- [ ] **Step 1.7.2: Run generator; copy fixtures to TS side**

```bash
cd packages/python/goldenmatch
python tests/parity/memory/gen_memory_fixtures.py --rebuild-db
mkdir -p ../../typescript/goldenmatch/tests/parity/fixtures
cp tests/parity/memory/fixtures/* ../../typescript/goldenmatch/tests/parity/fixtures/
```

Both fixture sets committed as binary/text into git.

- [ ] **Step 1.7.3: TS JSON parity test (`tests/parity/memory_json.parity.test.ts`)**

Read the JSON, run `correctionFromJSON` -> `correctionToJSON`, assert byte-equal to source.

- [ ] **Step 1.7.4: TS SQLite parity test (`tests/parity/memory_sqlite.parity.test.ts`)**

Open `memory.db` via `better-sqlite3`, fetch all corrections, assert each maps to the JSON fixture (after column-name translation snake_case -> camelCase).

- [ ] **Step 1.7.5: TS apply-outcome parity test (`tests/parity/memory_apply.parity.test.ts`)**

Load `memory_apply_inputs.json` (df, scored pairs, expected output). Seed an `InMemoryStore` from `memory_corrections.json`. Run `applyCorrections`. Assert resulting `(adjustedPairs, stats)` matches expected JSON byte-for-byte (sort `stalePairs` for determinism).

- [ ] **Step 1.7.6: Python parity tests (3 files mirroring TS)**

`test_memory_json_parity.py`, `test_memory_sqlite_parity.py`, `test_memory_apply_parity.py`. Same checks against the same fixtures.

- [ ] **Step 1.7.7: Run all parity tests on both sides; both green**

```bash
cd packages/typescript/goldenmatch && npx vitest run tests/parity/
cd packages/python/goldenmatch && pytest tests/parity/memory/ -v
```

- [ ] **Step 1.7.8: Commit**

```bash
git commit -m "test(memory): cross-language parity harness (JSON + SQLite + apply-outcome)"
```

### Phase 1.8: PR 1 wrap

- [ ] **Step 1.8.1: Update `src/core/index.ts`**

Replace the v0.3.1 re-exports with new symbols. Add a new `src/core/memory/index.ts` barrel:

```typescript
export * from "./types.js";
export * from "./store.js";
export * from "./corrections.js";
export * from "./learner.js";
export * from "./hash.js";
```

`src/core/index.ts:303-304`:
```typescript
export * from "./memory/index.js";
```

- [ ] **Step 1.8.2: Update `tsconfig.json` lib**

Add `"ESNext.Disposable"` to `compilerOptions.lib` (PR 2 needs `using`).

- [ ] **Step 1.8.3: Run full TS suite + typecheck**

```bash
cd packages/typescript/goldenmatch && npx tsc --noEmit && npx vitest run
```

- [ ] **Step 1.8.4: Push branch + open PR 1**

```bash
GH_TOKEN=$(gh auth token --user benzsevern) git push -u origin feature/ts-parity-learning-memory
gh pr create --title "refactor(memory): foundation rewrite + cross-language parity harness (BREAKING)" --body "..."
```

PR body lists breaking changes (`Correction` shape, `MemoryStore` async, FNV-1a -> SHA-256), new parity tests, explicit "no pipeline integration yet — PR 2 wires this up."

- [ ] **Step 1.8.5: Wait CI green; squash-merge.**

---

## PR 2: Pipeline + postflight + collection points

### Phase 2.1: DedupeResult / MatchResult fields

- [ ] **Step 2.1.1: Add `memoryStats: CorrectionStats | null` to result interfaces**

Modify `src/core/types.ts` (or wherever the interfaces live).

- [ ] **Step 2.1.2: Failing test: result types accept memoryStats**

- [ ] **Step 2.1.3: Pass; commit.**

### Phase 2.2: Async pipeline + memory hooks

This is the structurally biggest change. `runDedupePipeline` and `runMatchPipeline` go from sync to async. Every caller updates.

- [ ] **Step 2.2.1: Add `_applyMemoryPre` and `_applyMemoryPost` helpers near top of `pipeline.ts`**

```typescript
async function _applyMemoryPre(
  config: GoldenMatchConfig, matchkeys: MatchkeyConfig[], store: MemoryStore | null,
): Promise<void> {
  if (!store || !config.memory?.enabled) return;
  const learner = new MemoryLearner(store, config.memory.learning);
  if (!(await learner.hasNewCorrections())) return;
  const adjustments = await learner.learn();
  for (const adj of adjustments) {
    if (adj.threshold == null) continue;
    for (const mk of matchkeys) {
      if (mk.threshold == null) continue;
      if (!adj.matchkeyName || adj.matchkeyName === mk.name || adj.matchkeyName === "_default") {
        mk.threshold = adj.threshold; // in-place mutation
      }
    }
  }
}

async function _applyMemoryPost(
  config: GoldenMatchConfig, scoredPairs: ScoredPair[], df: Row[], matchkeyFields: string[], store: MemoryStore | null,
): Promise<readonly [ScoredPair[], CorrectionStats | null]> {
  if (!store || !config.memory?.enabled) return [scoredPairs, null];
  try {
    return await applyCorrections(scoredPairs, store, df, matchkeyFields, {
      dataset: config.memory.dataset ?? null,
      reanchor: config.memory.reanchor ?? true,
    });
  } catch (e) {
    console.warn(`Memory applyCorrections failed: ${e}`);
    return [scoredPairs, { applied: 0, stale: 0, staleAmbiguous: 0, staleUnanchorable: 0,
                           stalePairs: [], totalPairs: scoredPairs.length,
                           failed: true, error: String(e) }];
  }
}
```

- [ ] **Step 2.2.2: Make `runDedupePipeline` async; insert hooks**

`export function runDedupePipeline(...)` becomes `export async function runDedupePipeline(...)`. `_applyMemoryPre` runs before scoring loop; `_applyMemoryPost` runs at the scoring -> clustering boundary (~line 290 in current pipeline.ts).

- [ ] **Step 2.2.3: Make `runMatchPipeline` async** (same pattern).

- [ ] **Step 2.2.4: Update all callers**

`src/core/api.ts::dedupe`, `match`, `dedupeFile`, `matchFile` -> `async`. Their callers update.

`src/cli.ts` — commander handlers `await` the pipeline.

`src/node/dedupe-file.ts` — `await`.

Anywhere else that calls these functions — `await`.

- [ ] **Step 2.2.5: Failing test: pipeline applies seeded correction**

`tests/unit/memory-pipeline.test.ts`:

```typescript
it("seeded correction overrides scored pair on re-run", async () => {
  const store = new InMemoryStore();
  await store.addCorrection({
    id: "x", idA: 0, idB: 1, decision: "reject", source: "steward", trust: 1.0,
    fieldHash: "", recordHash: "", originalScore: 0.95,
    matchkeyName: null, reason: null, dataset: null, createdAt: new Date(),
  });

  const result = await dedupe(
    [
      { name: "Acme Corp", zip: "10001" },
      { name: "Acme LLC", zip: "10001" },
      { name: "Beta", zip: "20002" },
    ],
    { matchkeys: [/* identity, jaroWinkler, threshold 0.85 */],
      blocking: { strategy: "static", keys: [{ fields: ["zip"], transforms: ["lowercase"] }] },
      memory: { enabled: true } },
    { memoryStore: store },
  );

  expect(result.memoryStats?.applied).toBe(1);
});
```

- [ ] **Step 2.2.6: Confirm passes**

- [ ] **Step 2.2.7: Add no-memory regression tests** (mirror Python's `test_pipeline_no_memory_stats_when_disabled` + `test_pipeline_memory_disabled_does_not_open_store`).

- [ ] **Step 2.2.8: Add corrupt-DB survival test** (mirror Python's `test_pipeline_survives_corrupt_memory_db`).

- [ ] **Step 2.2.9: Commit.**

### Phase 2.3: Postflight rendering

- [ ] **Step 2.3.1: Failing test for memory line in postflight string**

Cover: `"Memory:"` appears when `applied > 0`; line omitted when all counters zero; `"FAILED"` when `stats.failed`; `"stale-ambiguous"` count when `staleAmbiguous > 0`.

- [ ] **Step 2.3.2: Implement `renderMemoryLine(stats)` in `src/core/autoconfigVerify.ts`**

```typescript
function renderMemoryLine(stats: CorrectionStats | null): string | null {
  if (!stats) return null;
  if (stats.failed) return "Memory: FAILED -- see logs";
  const sum = stats.applied + stats.stale + stats.staleAmbiguous + stats.staleUnanchorable;
  if (sum === 0) return null;
  return `Memory: ${stats.applied} corrections applied, ${stats.stale} stale, ` +
         `${stats.staleAmbiguous} stale-ambiguous, ${stats.staleUnanchorable} unanchorable`;
}
```

Wire into existing `PostflightReport` toString / render. ASCII only.

- [ ] **Step 2.3.3: Run; pass.**

- [ ] **Step 2.3.4: Commit.**

### Phase 2.4: Collection points

Per surface, run the TDD cycle:
- **A.** Failing test: invoking surface with `memoryStore` writes a `Correction`. Assert via `store.countCorrections()`.
- **B.** Confirm fails.
- **C.** Add optional `memoryStore` parameter; wire `addCorrection` after existing logic with the source/trust mapping from the spec.
- **D.** Confirm passes.
- **E.** Run full memory test suite — no regressions.
- **F.** Commit per surface.

Surfaces in order:

- [ ] **2.4.1** ReviewQueue (`src/core/review-queue.ts`) — base class only; source `steward`, trust 1.0; full hashes from df.
- [ ] **2.4.2** unmergeRecord + unmergeCluster (`src/core/cluster.ts`) — source `unmerge`, trust 1.0; **empty hashes** (no df in scope).
- [ ] **2.4.3** llmScorePairs (`src/core/llm/scorer.ts`) — source `llm`, trust 0.5; full hashes.
- [ ] **2.4.4** REST `POST /reviews/decide` (`src/node/api/server.ts`) — source `steward`, trust 1.0; **empty hashes**.

Note: TS port has no `agent_approve_reject` MCP tool today, so the agent collection point is deferred.

### Phase 2.5: E2E tests (port 7 of Python's 8 scenarios)

- [ ] **Step 2.5.1: `tests/integration/memory-e2e.test.ts`**

Port from Python `test_memory_e2e.py`, skipping the BoostTab scenario:

1. happy path: dedupe -> reject pair -> re-run -> pair score 0.0
2. re-anchor on reorder
3. re-anchor + edit on matchkey field
4. trust conflict
5. threshold learning (12 seeded corrections; learner overlay applied)
6. no API key, deterministic explainer (PR 3 finishes wiring; in PR 2 just assert no crash)
7. postflight surfaces stats

- [ ] **Step 2.5.2: All 7 pass.**

- [ ] **Step 2.5.3: Commit.**

### Phase 2.6: PR 2 wrap

- [ ] **Step 2.6.1: Run full TS suite.** Confirm pre-existing tests still green, new ~15 added.

- [ ] **Step 2.6.2: Push, open PR 2, wait for CI, merge.**

PR body emphasizes the **async pipeline migration** as the primary breaking change beyond what PR 1 introduced.

---

## PR 3: Surfaces + v0.4.0 release

### Phase 3.1: Python API mirror

- [ ] **Step 3.1.1: Failing test: `addCorrection` writes to default-path store**

- [ ] **Step 3.1.2: Implement `getMemory`, `addCorrection`, `learn`, `memoryStats` in `src/core/api.ts`**

```typescript
import { SqliteMemoryStore } from "../node/memory/sqlite-store.js";
import { trustForSource, type Decision, type CorrectionSource } from "./memory/types.js";

export async function getMemory(opts?: { path?: string }): Promise<SqliteMemoryStore> {
  const store = new SqliteMemoryStore({ path: opts?.path ?? ".goldenmatch/memory.db" });
  await store.init();
  return store;
}

export async function addCorrection(args: {
  idA: number; idB: number; decision: Decision;
  source?: CorrectionSource; reason?: string; dataset?: string;
  matchkeyName?: string; path?: string;
}): Promise<void> {
  const source = args.source ?? "api";
  const store = await getMemory({ path: args.path });
  try {
    await store.addCorrection({
      id: crypto.randomUUID(),
      idA: args.idA, idB: args.idB,
      decision: args.decision, source, trust: trustForSource(source),
      fieldHash: "", recordHash: "", originalScore: 0,
      matchkeyName: args.matchkeyName ?? null,
      reason: args.reason ?? null,
      dataset: args.dataset ?? null,
      createdAt: new Date(),
    });
  } finally {
    await store.close?.();
  }
}

export async function learn(args?: { matchkeyName?: string; path?: string }): Promise<LearnedAdjustment[]> {
  const store = await getMemory({ path: args?.path });
  try {
    const learner = new MemoryLearner(store, /* config defaults */);
    return await learner.learn(args?.matchkeyName);
  } finally {
    await store.close?.();
  }
}

export async function memoryStats(args?: { path?: string }): Promise<{
  count: number; lastLearnTime: Date | null; adjustments: LearnedAdjustment[];
}> {
  const store = await getMemory({ path: args?.path });
  try {
    return {
      count: await store.countCorrections(),
      lastLearnTime: await store.lastLearnTime(),
      adjustments: await store.getAllAdjustments(),
    };
  } finally {
    await store.close?.();
  }
}
```

Re-export from `src/index.ts`.

- [ ] **Step 3.1.3: Run; pass.**

- [ ] **Step 3.1.4: Commit.**

### Phase 3.2: CLI subgroup

- [ ] **Step 3.2.1: Add `memory` subgroup with 5 subcommands in `src/cli.ts`**

Pattern: `program.command("memory").addCommand(...)`. Each subcommand calls into `getMemory` / `addCorrection` / `learn` / `memoryStats`. Use `chalk` or commander's built-in formatting for output.

- [ ] **Step 3.2.2: Failing CLI tests**

Use commander's programmatic test API: `program.parseAsync(["node", "cli", "memory", "stats", "--path", "..."])` and assert console output (use vitest's `vi.spyOn(console, "log")`).

- [ ] **Step 3.2.3: Implement; passes.**

- [ ] **Step 3.2.4: Commit.**

### Phase 3.3: Five MCP tools

- [ ] **Step 3.3.1: Failing test: `MEMORY_TOOLS` exports 5 named tools**

```typescript
import { MEMORY_TOOLS, MEMORY_TOOL_NAMES } from "../../src/node/mcp/memory-tools.js";

it("exports 5 named tools", () => {
  const names = MEMORY_TOOLS.map((t) => t.name).sort();
  expect(names).toEqual(["add_correction", "learn_thresholds",
                         "list_corrections", "memory_export", "memory_stats"]);
  expect(MEMORY_TOOL_NAMES.size).toBe(5);
});
```

- [ ] **Step 3.3.2: Implement `src/node/mcp/memory-tools.ts`**

Mirror Python `mcp/memory_tools.py` exactly: 5 `Tool` objects with `inputSchema`, plus `handleMemoryTool(name, args)` async dispatcher returning `Promise<TextContent[]>`. Each handler instantiates its own `SqliteMemoryStore`, traps SQLite errors and returns structured `TextContent` rather than crashing.

- [ ] **Step 3.3.3: Wire into `server.ts`**

```typescript
import { MEMORY_TOOLS, MEMORY_TOOL_NAMES, handleMemoryTool } from "./memory-tools.js";

export const TOOLS: readonly Tool[] = [...EXISTING_TOOLS, ...MEMORY_TOOLS];

// in dispatch:
if (MEMORY_TOOL_NAMES.has(name)) return handleMemoryTool(name, args);
```

- [ ] **Step 3.3.4: Update server description literal at line 6**

Count `TOOLS.length` and update `* Exposes ~20 tools covering ...` to `* Exposes <N> tools covering ..., learning memory, ...`.

- [ ] **Step 3.3.5: Add registration test**

```typescript
import { readFileSync } from "fs";
import { TOOLS } from "../../src/node/mcp/server.js";

it("server description literal matches actual tool count", () => {
  const src = readFileSync("src/node/mcp/server.ts", "utf-8");
  const m = src.match(/Exposes (\d+) tools/);
  expect(m).toBeTruthy();
  expect(Number(m![1])).toBe(TOOLS.length);
});
```

- [ ] **Step 3.3.6: End-to-end MCP add+list test**

```typescript
it("MCP add_correction then list_corrections round-trips", async () => {
  const out1 = await handleMemoryTool("add_correction", {
    id_a: 1, id_b: 2, decision: "approve", dataset: "test",
  });
  const out2 = await handleMemoryTool("list_corrections", { dataset: "test" });
  expect(out2[0]?.text).toContain("\"id_a\": 1");
});
```

- [ ] **Step 3.3.7: Commit.**

### Phase 3.4: Explainer integration

- [ ] **Step 3.4.1: Add `why?: string` to `ReviewItem` in `src/core/review-queue.ts`**

- [ ] **Step 3.4.2: Add `whyForCorrection(correction, df, matchkeyFields, { useLlm? })`**

Default deterministic prose ("matched on name / zip with score 0.92"); LLM upgrade via `llmExplainPair` when `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` is set and config enables it.

- [ ] **Step 3.4.3: Tests + commit.**

### Phase 3.5: v0.4.0 release wrap

- [ ] **Step 3.5.1: Bump `package.json` version `0.3.1` -> `0.4.0`**

- [ ] **Step 3.5.2: CHANGELOG entry**

```markdown
## [0.4.0] - 2026-05-XX

### BREAKING
- `Correction.verdict` renamed to `Correction.decision` ("approve" | "reject")
- `Correction.feature` renamed to `Correction.matchkeyName`
- `MemoryStore` interface methods are now async
- `runDedupePipeline` and `runMatchPipeline` are now async
- `dedupe`, `match`, `dedupeFile`, `matchFile` API functions are now async
- Hash algorithm changed from FNV-1a to SHA-256 (cross-language storage parity with Python)

### Added
- Pipeline integration for Learning Memory (`config.memory.enabled = true`)
- Five MCP tools: list_corrections, add_correction, learn_thresholds, memory_stats, memory_export
- CLI subgroup: `goldenmatch-js memory <stats|learn|export|import|show>`
- Python API mirror: getMemory, addCorrection, learn, memoryStats
- SqliteMemoryStore (Node only; requires better-sqlite3 peer dep)
- Cross-language parity tests (JSON, SQLite, apply-outcome)
- Postflight rendering: "Memory: N applied, M stale, K stale-ambiguous, J unanchorable"
- Re-anchoring: corrections survive row reordering across runs
- CorrectionStats.staleAmbiguous and staleUnanchorable counters
```

- [ ] **Step 3.5.3: Push, open PR 3, wait for CI, merge.**

- [ ] **Step 3.5.4: After merge, tag and release**

```bash
gh auth switch --user benzsevern
git tag goldenmatch-js-v0.4.0
GH_TOKEN=$(gh auth token --user benzsevern) git push origin goldenmatch-js-v0.4.0
gh release create goldenmatch-js-v0.4.0 --target main \
  --title "goldenmatch-js v0.4.0 -- Learning Memory parity with Python v1.6.0" \
  --notes-file release-notes.md
```

If `publish-npm.yml` is wired (likely from pre-fold), it auto-publishes on tag. If not, follow the same diagnostic path PR #71 took for `publish.yml` — check whether the workflow is at the monorepo root or orphaned at the package level.

---

## Out of plan (deferred per spec)

- BoostTab parity — TS TUI has no boost surface
- Rules layer — Python doesn't have it either
- Postgres backend in TS
- Web review surface
- Cross-runtime concurrent-write WAL guarantees
- Agent MCP `agent_approve_reject` parity (not present in TS)

---

## Risk register

- **Async pipeline migration is a 2nd breaking change** beyond field renames. Every external caller of `dedupe()` / `match()` / `runDedupePipeline()` updates. CHANGELOG must call this out as the primary breaking change.
- **`better-sqlite3` native compile failures on Windows.** Same risk as `hnswlib-node`. Document install guidance in README; CI Linux runner compiles fine.
- **`memory.db` regeneration determinism.** Generator clamps every source of nondeterminism (fixed UUIDs, fixed timestamps, no `datetime.now()`). CI fails loudly on byte-mismatch. JSON drift catches a regeneration-without-`--rebuild-db` mistake.
- **`crypto.randomUUID` in Node 20.** Available natively. If targeting older Node, swap to a UUIDv4 lib.
- **Web Crypto async overhead at 100K+ rows.** Mitigation: `computeRecordHashes` uses `Promise.all` for parallelism. If still too slow, add a Node-only sync path via `node:crypto` in `src/node/memory/hash-node.ts` and let `core/memory/hash.ts` re-export it via runtime detection.
- **PR 2 ↔ PR 3 ordering.** Phase 2 e2e tests don't exercise CLI/MCP surfaces; they seed via direct `addCorrection`. Phase 3 e2e adds CLI/MCP smoke tests. Each PR ships independently green.
- **`tsup` bundle size.** Adding `memory-tools.ts` adds ~30KB. Acceptable. SQLite store lives in `src/node/` so it's only in the Node bundle entry point.
