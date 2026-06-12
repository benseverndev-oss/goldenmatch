# GoldenCheck TS Port: Missing Profilers / Relations + `validate` MCP Tool Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the TypeScript `goldencheck` port to module-parity with the Python source of truth by porting 2 profilers (`freshness`, `fuzzy_values`), 4 relations (`approx_duplicate`, `approx_fd`, `composite_key`, `functional_dependency`), and the `validate` MCP tool — and harden the parity harness so these never silently regress.

**Architecture:** Each module is a faithful behavior-port of its Python sibling onto the edge-safe `TabularData` abstraction. Column profilers implement `Profiler`; relations implement `RelationProfiler`; both are registered in their index files (the scanner consumes those registries unchanged). The native Rust kernels are Python-only by design (issue #855 "Out of scope") — the TS port mirrors the Python **fallback** path, which the Python CLAUDE.md guarantees is byte-identical to the kernel. Parity is verified two ways: (1) TS unit tests mirroring the Python unit tests, (2) the CSV golden harness extended with cases for the modules that survive a CSV round-trip.

**Tech Stack:** TypeScript (NodeNext ESM, `.js` import suffixes), vitest, the existing `TabularData` / `Finding` / `makeFinding` core, the hand-rolled JSON-RPC MCP server, and the Python `gen_parity_goldens_js.py` golden generator (Polars).

---

## Background & Source-of-Truth Map

| TS file to CREATE | Python source of truth | Python unit test to mirror |
|---|---|---|
| `src/core/profilers/freshness.ts` | `goldencheck/profilers/freshness.py` | `tests/profilers/test_freshness.py` |
| `src/core/profilers/fuzzy-values.ts` | `goldencheck/profilers/fuzzy_values.py` | `tests/profilers/test_fuzzy_values.py` |
| `src/core/relations/approx-duplicate.ts` | `goldencheck/relations/approx_duplicate.py` | `tests/relations/test_approx_duplicate.py` |
| `src/core/relations/functional-dependency.ts` | `goldencheck/relations/functional_dependency.py` | `tests/relations/test_functional_dependency.py` |
| `src/core/relations/composite-key.ts` | `goldencheck/relations/composite_key.py` | `tests/relations/test_composite_key.py` |
| `src/core/relations/approx-fd.ts` | `goldencheck/relations/approx_fd.py` | `tests/relations/test_approx_fd.py` |
| `validate` tool in `src/node/mcp/server.ts` | `goldencheck/mcp/server.py` `_tool_validate` | (new TS test) |

**Files MODIFIED (registries + assertions):**
- `src/core/profilers/index.ts` — register `FuzzyValuesProfiler`, `FreshnessProfiler` (Python order: fuzzy then freshness, after the 10 base profilers).
- `src/core/relations/index.ts` — register `CompositeKeyProfiler`, `ApproxDuplicateProfiler`, `FunctionalDependencyProfiler`, `ApproximateFDProfiler` (Python order, after `IdentitySafePkProfiler`).
- `src/core/data.ts` — add one additive method `nUniqueTuple(cols)` (used by composite-key + functional-dependency).
- `tests/unit/profilers/all-profilers.test.ts` — `COLUMN_PROFILERS.length` 10 → 12.
- `tests/unit/relations/all-relations.test.ts` — `RELATION_PROFILERS.length` 5 → 9.
- `tests/unit/mcp-agent-tools.test.ts` — `TOOL_DEFINITIONS.length` 17 → 18 (now "8 core + 10 agent").
- `scripts/gen_parity_goldens_js.py` (Python pkg) — emit `affected_rows` per finding.
- `tests/parity/parity.test.ts` — assert `confidence` + `affectedRows`; **fail** (not skip) when manifest/golden missing.
- `tests/fixtures/parity_cases.json` — add cases for the 5 CSV-survivable modules.
- `tests/fixtures/_goldens_js/*.json` — regenerated goldens (committed).
- `packages/python/goldencheck/CLAUDE.md` "TypeScript Port" section + `packages/typescript/goldencheck/` CHANGELOG — note the new parity surface.

### Parity ground rules (read before writing any code)

1. **Native is out of scope.** Port only the Python fallback path. Do NOT add a native loader, env gate, or kernel call to the TS modules. The CLAUDE.md guarantees kernel output is byte-identical to the fallback, so a golden generated with the kernel present still matches the TS fallback port.

2. **Freshness is EXCLUDED from the CSV golden harness.** `pl.read_csv` defaults to `try_parse_dates=False`, so the golden generator reads ISO-date strings as `Utf8` — Python's `FreshnessProfiler` gates on `pl.Date`/`pl.Datetime` and therefore emits nothing through the CSV path. But the TS `TabularData.dtype()` returns `"date"`/`"datetime"` for ISO strings, so a freshness golden would mismatch. Freshness correctness is verified by **unit tests only** (mirroring `test_freshness.py`). Document this in the parity test file as a comment. (All five other modules survive the CSV round-trip — they gate on string/int/bool dtypes, which Polars CSV inference and `TabularData.dtype()` agree on.)

3. **Connected-component invariance (fuzzy).** The fuzzy union-find produces order-independent connected components, so TS need not match Python's `set` iteration order. Keep the fuzzy parity fixture to a **single** cluster so the 5-cluster cap and inter-cluster ordering never affect the asserted `affectedRows`.

4. **No floats / no nulls in relation parity fixtures.** `nUniqueTuple` and the interner stringify values; Polars formats floats (`1.0`) differently from JS (`1`). Use only string and integer columns, with no nulls, in the `composite_key` / `functional_dependency` / `approx_fd` / `approx_duplicate` parity fixtures so distinctness classes match exactly. (Booleans are supported by the ports but avoided in fixtures.)

5. **Confidence parity is downstream of finding-set parity.** Corroboration boost and confidence downgrade run identically on both sides; if the finding sets match, the rounded-to-4 confidences match. Keep fixtures shaped so each target finding's column carries no surprise corroborating WARNING/ERROR.

---

## Prerequisites

- [ ] **Branch:** Execute on a fresh branch `fix/855-goldencheck-ts-port` cut from `main` (per the Golden Suite branch/merge SOP). The current working tree carries unrelated #857 changes — start from a clean checkout/worktree.
- [ ] **TS setup:** `cd packages/typescript/goldencheck && npm install`. Sanity: `npm run test` (baseline green), `npm run typecheck`.
- [ ] **Python setup (for goldens only):** the repo's goldencheck venv with Polars. On Windows set `POLARS_SKIP_CPU_CHECK=1` to avoid the WMI import hang. The golden generator runs the Python **fallback** (native kernel is not built locally), which is exactly what TS mirrors.

All `npm` commands below run from `packages/typescript/goldencheck/`. Single-file test runs use `npx vitest run <path>`.

---

## Task 1: `nUniqueTuple` helper on TabularData

Both `composite-key` and `functional-dependency` need "distinct count over a set of columns" (Polars `df.select(cols).n_unique()`). Add one additive method to the shared data wrapper.

**Files:**
- Modify: `src/core/data.ts` (add a method to the `TabularData` class)
- Test: `tests/unit/data.test.ts` (append)

- [ ] **Step 1: Write the failing test** — append to `tests/unit/data.test.ts`:

```ts
describe("nUniqueTuple", () => {
  it("counts distinct value-tuples across columns (mirrors df.select(cols).n_unique())", () => {
    const data = new TabularData([
      { a: 1, b: "x" },
      { a: 1, b: "x" }, // dup tuple
      { a: 1, b: "y" },
      { a: 2, b: "x" },
    ]);
    expect(data.nUniqueTuple(["a", "b"])).toBe(3); // (1,x),(1,y),(2,x)
    expect(data.nUniqueTuple(["a"])).toBe(2);
  });

  it("treats null consistently as a single group", () => {
    const data = new TabularData([
      { a: null, b: "x" },
      { a: null, b: "x" },
      { a: 1, b: "x" },
    ]);
    expect(data.nUniqueTuple(["a", "b"])).toBe(2); // (null,x),(1,x)
  });
});
```

- [ ] **Step 2: Run it to confirm it fails** — `npx vitest run tests/unit/data.test.ts`. Expected: FAIL, `nUniqueTuple is not a function`.

- [ ] **Step 3: Implement** — add to the `TabularData` class in `src/core/data.ts` (place near `nUnique`):

```ts
  /**
   * Count distinct value-tuples over a set of columns.
   * Mirrors Polars `df.select(cols).n_unique()`: null is a single distinct
   * group; non-null values are compared by their String() form (callers must
   * not pass float columns where JS/Polars formatting would diverge).
   */
  nUniqueTuple(cols: readonly string[]): number {
    const colVals = cols.map((c) => this.column(c));
    const seen = new Set<string>();
    const n = this.rowCount;
    for (let i = 0; i < n; i++) {
      let key = "";
      for (let c = 0; c < colVals.length; c++) {
        const v = colVals[c]![i];
        key += (v === null ? " NULL" : String(v)) + "";
      }
      seen.add(key);
    }
    return seen.size;
  }
```

- [ ] **Step 4: Run it to confirm it passes** — `npx vitest run tests/unit/data.test.ts`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/core/data.ts tests/unit/data.test.ts
git commit -m "feat(ts): add TabularData.nUniqueTuple for composite-key/FD relations (#855)"
```

---

## Task 2: Freshness profiler

Port of `freshness.py`. Date/datetime columns: `future_dated` (WARNING, always-on) + `stale_data` (INFO, name-gated). Dates arrive in `TabularData` as ISO strings; parse with `Date.parse` and compare epoch-ms. **Not** in the CSV golden harness (see ground rule 2).

**Files:**
- Create: `src/core/profilers/freshness.ts`
- Modify: `src/core/profilers/index.ts`
- Test: `tests/unit/profilers/freshness.test.ts`

- [ ] **Step 1: Write the failing test** — `tests/unit/profilers/freshness.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { TabularData } from "../../../src/core/data.js";
import { Severity } from "../../../src/core/types.js";
import { FreshnessProfiler } from "../../../src/core/profilers/freshness.js";

const checks = (fs: { check: string }[]) => new Set(fs.map((f) => f.check));

describe("FreshnessProfiler", () => {
  const profiler = new FreshnessProfiler();

  it("flags future-dated values (datetime)", () => {
    const data = new TabularData([
      { order_ts: "2020-01-01T00:00:00" },
      { order_ts: "2099-01-01T00:00:00" },
    ]);
    const findings = profiler.profile(data, "order_ts");
    expect(checks(findings).has("future_dated")).toBe(true);
    const f = findings.find((x) => x.check === "future_dated")!;
    expect(f.affectedRows).toBe(1);
    expect(f.severity).toBe(Severity.WARNING);
    expect(f.confidence).toBe(0.7);
  });

  it("flags future-dated values (date)", () => {
    const data = new TabularData([{ d: "2020-06-01" }, { d: "2099-01-01" }]);
    expect(checks(profiler.profile(data, "d")).has("future_dated")).toBe(true);
  });

  it("is silent on old, non-update date columns", () => {
    const data = new TabularData([{ d: "2020-01-01" }, { d: "2021-06-01" }]);
    expect(profiler.profile(data, "d")).toEqual([]);
  });

  it("flags staleness on update/event columns", () => {
    // ~800 days before a fixed-but-old anchor; both far past, so always stale.
    const data = new TabularData([
      { updated_at: "2001-01-01" },
      { updated_at: "2000-12-27" },
    ]);
    const findings = profiler.profile(data, "updated_at");
    expect(checks(findings).has("stale_data")).toBe(true);
    const f = findings.find((x) => x.check === "stale_data")!;
    expect(f.severity).toBe(Severity.INFO);
    expect(f.confidence).toBe(0.5);
    expect(f.affectedRows).toBe(2);
  });

  it("does NOT flag an old non-update column as stale", () => {
    const data = new TabularData([{ birth_date: "2001-01-01" }]);
    expect(checks(profiler.profile(data, "birth_date")).has("stale_data")).toBe(false);
  });

  it("skips non-temporal columns", () => {
    const data = new TabularData([{ n: 1 }, { n: 2 }, { n: 3 }]);
    expect(profiler.profile(data, "n")).toEqual([]);
    const s = new TabularData([{ s: "a" }, { s: "b" }, { s: "c" }]);
    expect(profiler.profile(s, "s")).toEqual([]);
  });
});
```

- [ ] **Step 2: Run it to confirm it fails** — `npx vitest run tests/unit/profilers/freshness.test.ts`. Expected: FAIL, cannot find module `freshness.js`.

- [ ] **Step 3: Implement** — `src/core/profilers/freshness.ts`:

```ts
/**
 * Freshness / staleness profiler for date & datetime columns.
 * Port of goldencheck/profilers/freshness.py (pure-JS; no native kernel).
 *
 * - future_dated (WARNING, always on): values after "now" — clock skew / typos.
 * - stale_data (INFO, name-gated): newest value on an update/event column is
 *   more than STALE_DAYS old.
 *
 * Dates arrive as ISO strings in TabularData (data.dtype() reports "date" /
 * "datetime" via ISO regex), so we parse with Date.parse and compare epoch-ms.
 */
import type { TabularData } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { Profiler } from "./base.js";

const STALE_DAYS = 365;
const MS_PER_DAY = 86_400_000;

// Column-name signals that the timestamp tracks "last change", so old == stale.
const UPDATE_KEYWORDS = [
  "updated", "modified", "last_seen", "lastseen", "last_login", "lastlogin",
  "ingested", "loaded", "refreshed", "synced", "as_of", "asof", "event",
  "timestamp", "created", "inserted",
];

function looksLikeUpdateColumn(name: string): boolean {
  const lower = name.toLowerCase();
  return UPDATE_KEYWORDS.some((kw) => lower.includes(kw));
}

export class FreshnessProfiler implements Profiler {
  profile(data: TabularData, column: string): Finding[] {
    const dt = data.dtype(column);
    if (dt !== "date" && dt !== "datetime") return [];

    const nonNull = data.dropNulls(column);
    if (nonNull.length === 0) return [];

    const now = Date.now();
    let futureCount = 0;
    let newestMs = -Infinity;
    let newestRaw: string | null = null;
    for (const v of nonNull) {
      const ms = Date.parse(String(v));
      if (!Number.isFinite(ms)) continue;
      if (ms > now) futureCount++;
      if (ms > newestMs) {
        newestMs = ms;
        newestRaw = String(v);
      }
    }
    if (newestRaw === null) return [];

    const findings: Finding[] = [];

    if (futureCount > 0) {
      findings.push(
        makeFinding({
          severity: Severity.WARNING,
          column,
          check: "future_dated",
          message:
            `${futureCount} value(s) in '${column}' are in the future ` +
            `(newest: ${newestRaw}) — likely clock skew or a data-entry error.`,
          affectedRows: futureCount,
          sampleValues: [newestRaw],
          suggestion:
            "Verify the source clock/timezone, or treat future-dated rows as invalid.",
          confidence: 0.7,
          metadata: { technique: "freshness", future_count: futureCount },
        }),
      );
    }

    if (looksLikeUpdateColumn(column)) {
      const ageDays = Math.floor((now - newestMs) / MS_PER_DAY);
      if (ageDays > STALE_DAYS) {
        findings.push(
          makeFinding({
            severity: Severity.INFO,
            column,
            check: "stale_data",
            message:
              `Newest '${column}' is ${ageDays} days old (${newestRaw}) — ` +
              `this update/event timestamp suggests the data may be stale.`,
            affectedRows: nonNull.length,
            sampleValues: [newestRaw],
            suggestion: "Confirm the pipeline feeding this table is still running.",
            confidence: 0.5,
            metadata: { technique: "freshness", age_days: ageDays },
          }),
        );
      }
    }

    return findings;
  }
}
```

- [ ] **Step 4: Run it to confirm it passes** — `npx vitest run tests/unit/profilers/freshness.test.ts`. Expected: PASS.

- [ ] **Step 5: Register in the profiler registry** — edit `src/core/profilers/index.ts`: add the re-export, the import, and append to `COLUMN_PROFILERS` (mirror Python order: fuzzy first, then freshness — but FuzzyValues lands in Task 3; for now add only `FreshnessProfiler` at the end and reorder in Task 3). To avoid churn, add `FreshnessProfiler` now as the last entry; Task 3 inserts `FuzzyValuesProfiler` immediately before it.

```ts
export { FreshnessProfiler } from "./freshness.js";
// ...
import { FreshnessProfiler } from "./freshness.js";
// ...append inside COLUMN_PROFILERS:
  // Freshness: future-dated values + (name-gated) staleness on date/datetime cols.
  new FreshnessProfiler(),
```

- [ ] **Step 6: Update the registry count assertion** — in `tests/unit/profilers/all-profilers.test.ts`, the `COLUMN_PROFILERS` "has exactly 10 profilers" test will become 12 after Task 3. For now bump to 11 and add the import-free registry run-through, OR (preferred) leave this assertion update to Task 3 Step 6 where both new profilers are registered. **Choose: defer the count assertion to Task 3.** Here, only verify the registry still loads: `npx vitest run tests/unit/profilers/all-profilers.test.ts` will FAIL the count assertion (10 vs 11). To keep commits green, bump it to 11 in this commit and to 12 in Task 3.

  Edit `all-profilers.test.ts`: `expect(COLUMN_PROFILERS.length).toBe(11);`

- [ ] **Step 7: Run the profiler suite** — `npx vitest run tests/unit/profilers/`. Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/core/profilers/freshness.ts src/core/profilers/index.ts \
  tests/unit/profilers/freshness.test.ts tests/unit/profilers/all-profilers.test.ts
git commit -m "feat(ts): port freshness profiler (future_dated + stale_data) (#855)"
```

---

## Task 3: Fuzzy-values profiler

Port of `fuzzy_values.py` `_python_clusters` (the fallback): normalize → trigram + 2-char-prefix blocking → Levenshtein-ratio ≥ 0.82 → union-find clusters. Guards: ≥50 rows, string dtype, distinct count in [3, 2000]. One finding per cluster (largest first, capped at 5).

**Files:**
- Create: `src/core/profilers/fuzzy-values.ts`
- Modify: `src/core/profilers/index.ts`
- Test: `tests/unit/profilers/fuzzy-values.test.ts`

- [ ] **Step 1: Write the failing test** — `tests/unit/profilers/fuzzy-values.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { TabularData } from "../../../src/core/data.js";
import { Severity } from "../../../src/core/types.js";
import { FuzzyValuesProfiler } from "../../../src/core/profilers/fuzzy-values.js";

function stateData(n = 120): TabularData {
  const variants = ["California", "Californa", "CALIFORNIA", "Texas", "New York"];
  const rows = Array.from({ length: n }, (_, i) => ({
    state: variants[i % variants.length]!,
    clean: ["apple", "banana", "cherry"][i % 3]!,
  }));
  return new TabularData(rows);
}

describe("FuzzyValuesProfiler", () => {
  const profiler = new FuzzyValuesProfiler();

  it("flags near-duplicate value variants", () => {
    const findings = profiler.profile(stateData(), "state");
    expect(findings.length).toBeGreaterThan(0);
    const f = findings[0]!;
    expect(f.check).toBe("fuzzy_duplicate_values");
    expect(f.severity).toBe(Severity.WARNING);
    expect(f.confidence).toBe(0.6);
    const variants = new Set(f.metadata["variants"] as string[]);
    expect(variants.has("California")).toBe(true);
    expect(variants.has("Californa")).toBe(true);
    expect(variants.has("CALIFORNIA")).toBe(true);
    // 3 spellings of California across 120 rows cycling through 5 variants:
    // indices 0,2 (California, CALIFORNIA) + 1 (Californa) → 3 of every 5 rows.
    expect(f.affectedRows).toBe(72);
  });

  it("is silent on a clean categorical column", () => {
    expect(profiler.profile(stateData(), "clean")).toEqual([]);
  });

  it("skips non-string columns", () => {
    const data = new TabularData(Array.from({ length: 100 }, (_, i) => ({ n: i })));
    expect(profiler.profile(data, "n")).toEqual([]);
  });

  it("skips columns below the row floor", () => {
    expect(profiler.profile(stateData(10), "state")).toEqual([]);
  });
});
```

> Note on `affectedRows`: 120 rows cycling `[California, Californa, CALIFORNIA, Texas, New York]`. The cluster is {California, Californa, CALIFORNIA} = positions 0,1,2 in the cycle = 3 of every 5 rows = 72. If your distinct-extraction or clustering differs you'll see a different number — that is the signal to fix the algorithm, not the test.

- [ ] **Step 2: Run it to confirm it fails** — `npx vitest run tests/unit/profilers/fuzzy-values.test.ts`. Expected: FAIL, cannot find module.

- [ ] **Step 3: Implement** — `src/core/profilers/fuzzy-values.ts`:

```ts
/**
 * Fuzzy near-duplicate VALUE detection (column profiler).
 * Port of goldencheck/profilers/fuzzy_values.py (the pure-Python fallback
 * `_python_clusters`; the native kernel is Python-only and byte-identical).
 *
 * Flags categorical string columns whose distinct values include
 * edit-distance-close variants ("California"/"Californa"/"CALIFORNIA"). Runs on
 * a column's DISTINCT values with trigram + 2-char-prefix blocking and a
 * Levenshtein-ratio scorer, then union-find clusters.
 */
import type { TabularData } from "../data.js";
import { isNullish } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { Profiler } from "./base.js";

const MIN_ROWS = 50;
const MIN_DISTINCT = 3;
const MAX_DISTINCT = 2000;
const MIN_SIMILARITY = 0.82;
const MAX_CLUSTERS_REPORTED = 5;

function normalize(s: string): string {
  return s.toLowerCase().replace(/\s+/g, " ").trim();
}

function levenshtein(a: string, b: string): number {
  if (a.length === 0) return b.length;
  if (b.length === 0) return a.length;
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 0; i < a.length; i++) {
    const cur = [i + 1];
    for (let j = 0; j < b.length; j++) {
      const cost = a[i] === b[j] ? 0 : 1;
      cur.push(Math.min(prev[j + 1]! + 1, cur[j]! + 1, prev[j]! + cost));
    }
    prev = cur;
  }
  return prev[b.length]!;
}

function levRatio(a: string, b: string): number {
  const maxlen = Math.max(a.length, b.length);
  if (maxlen === 0) return 1.0;
  return 1.0 - levenshtein(a, b) / maxlen;
}

/** Mirror of goldencheck_core::near_duplicate_clusters / the Python fallback. */
function clusters(values: readonly string[], minSimilarity: number): number[][] {
  const norm = values.map(normalize);
  const n = values.length;
  const trigram = new Map<string, number[]>();
  const prefix = new Map<string, number[]>();
  for (let i = 0; i < n; i++) {
    const s = norm[i]!;
    if (s.length < 3) continue;
    for (let k = 0; k < s.length - 2; k++) {
      const tg = s.slice(k, k + 3);
      (trigram.get(tg) ?? trigram.set(tg, []).get(tg)!).push(i);
    }
    const px = s.slice(0, 2);
    (prefix.get(px) ?? prefix.set(px, []).get(px)!).push(i);
  }

  // Candidate pairs from blocking buckets (size in [2, 300]); dedup via a Set.
  const candidates = new Set<number>(); // encode (i,j), i<j, as i*n+j
  for (const bucket of [...trigram.values(), ...prefix.values()]) {
    if (bucket.length < 2 || bucket.length > 300) continue;
    for (let a = 0; a < bucket.length; a++) {
      for (let b = a + 1; b < bucket.length; b++) {
        const i = bucket[a]!;
        const j = bucket[b]!;
        candidates.add(i < j ? i * n + j : j * n + i);
      }
    }
  }

  const parent = Array.from({ length: n }, (_, i) => i);
  const find = (x: number): number => {
    while (parent[x] !== x) {
      parent[x] = parent[parent[x]!]!;
      x = parent[x]!;
    }
    return x;
  };

  let linked = false;
  for (const enc of candidates) {
    const i = Math.floor(enc / n);
    const j = enc % n;
    if (levRatio(norm[i]!, norm[j]!) >= minSimilarity) {
      const ri = find(i);
      const rj = find(j);
      if (ri !== rj) parent[ri] = rj;
      linked = true;
    }
  }
  if (!linked) return [];

  const groups = new Map<number, number[]>();
  for (let i = 0; i < n; i++) {
    const r = find(i);
    (groups.get(r) ?? groups.set(r, []).get(r)!).push(i);
  }
  const out = [...groups.values()].filter((g) => g.length >= 2).map((g) => [...g].sort((a, b) => a - b));
  // Lexicographic sort of clusters (element-wise), mirroring Python clusters.sort().
  out.sort((x, y) => {
    const len = Math.min(x.length, y.length);
    for (let k = 0; k < len; k++) {
      if (x[k] !== y[k]) return x[k]! - y[k]!;
    }
    return x.length - y.length;
  });
  return out;
}

export class FuzzyValuesProfiler implements Profiler {
  profile(data: TabularData, column: string): Finding[] {
    if (data.rowCount < MIN_ROWS) return [];
    if (data.dtype(column) !== "string") return [];

    // Distinct non-null values, first-seen order.
    const seen = new Set<string>();
    const values: string[] = [];
    for (const v of data.column(column)) {
      if (isNullish(v)) continue;
      const s = String(v);
      if (!seen.has(s)) {
        seen.add(s);
        values.push(s);
      }
    }
    const nDistinct = values.length;
    if (nDistinct < MIN_DISTINCT || nDistinct > MAX_DISTINCT) return [];

    const found = clusters(values, MIN_SIMILARITY);
    if (found.length === 0) return [];

    // Largest clusters first (stable), report a bounded number.
    const ordered = [...found].sort((a, b) => b.length - a.length);
    const findings: Finding[] = [];
    const fullColumn = data.column(column);
    for (const cluster of ordered.slice(0, MAX_CLUSTERS_REPORTED)) {
      const variants = cluster.map((i) => values[i]!);
      const shown = variants.slice(0, 6);
      const variantSet = new Set(variants);
      let affected = 0;
      for (const v of fullColumn) {
        if (v !== null && variantSet.has(String(v))) affected++;
      }
      const ellipsis = variants.length > shown.length ? " …" : "";
      findings.push(
        makeFinding({
          severity: Severity.WARNING,
          column,
          check: "fuzzy_duplicate_values",
          message:
            `Column '${column}' has ${variants.length} near-duplicate values that look ` +
            `like variants of one another: ${shown.map((v) => `'${v}'`).join(", ")}${ellipsis}.`,
          affectedRows: affected,
          sampleValues: shown.map((v) => String(v)),
          suggestion:
            "Standardize these to a single canonical value (casing/spelling), " +
            "or define an enum, so they reconcile.",
          confidence: 0.6,
          metadata: { technique: "fuzzy_duplicate_values", variants },
        }),
      );
    }
    return findings;
  }
}
```

> Note: the `(map.get(k) ?? map.set(k, []).get(k)!)` idiom appends to an existing bucket or creates one. If your linter dislikes it, expand to an explicit `let bucket = map.get(k); if (!bucket) { bucket = []; map.set(k, bucket); } bucket.push(i);` — behavior is identical.

- [ ] **Step 4: Run it to confirm it passes** — `npx vitest run tests/unit/profilers/fuzzy-values.test.ts`. Expected: PASS. If `affectedRows` ≠ 72, your clustering diverged — re-check the trigram/prefix blocking and the 0.82 threshold against the Python `_python_clusters`.

- [ ] **Step 5: Register in the profiler registry** — edit `src/core/profilers/index.ts`: add export + import, and insert `new FuzzyValuesProfiler()` **immediately before** `new FreshnessProfiler()` in `COLUMN_PROFILERS` (Python order):

```ts
export { FuzzyValuesProfiler } from "./fuzzy-values.js";
import { FuzzyValuesProfiler } from "./fuzzy-values.js";
// in COLUMN_PROFILERS, before FreshnessProfiler:
  // Fuzzy near-duplicate VALUE detection (inconsistent categorical encodings).
  new FuzzyValuesProfiler(),
```

- [ ] **Step 6: Update the registry count assertion** — `tests/unit/profilers/all-profilers.test.ts`: `expect(COLUMN_PROFILERS.length).toBe(12);`

- [ ] **Step 7: Run the profiler suite** — `npx vitest run tests/unit/profilers/`. Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/core/profilers/fuzzy-values.ts src/core/profilers/index.ts \
  tests/unit/profilers/fuzzy-values.test.ts tests/unit/profilers/all-profilers.test.ts
git commit -m "feat(ts): port fuzzy-values profiler (near-dup categorical encodings) (#855)"
```

---

## Task 4: Approx-duplicate relation

Port of `approx_duplicate.py`. Whole-row exact (`duplicate_rows`) + normalized near-duplicate (`near_duplicate_rows`) detection. Emits on the synthetic `__dataset__` column. Pure-JS (Polars normalize+group-by → JS Map counting).

**Files:**
- Create: `src/core/relations/approx-duplicate.ts`
- Modify: `src/core/relations/index.ts`
- Test: `tests/unit/relations/approx-duplicate.test.ts`

- [ ] **Step 1: Write the failing test** — `tests/unit/relations/approx-duplicate.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { TabularData } from "../../../src/core/data.js";
import { ApproxDuplicateProfiler } from "../../../src/core/relations/approx-duplicate.js";

const checks = (fs: { check: string }[]) => new Set(fs.map((f) => f.check));

describe("ApproxDuplicateProfiler", () => {
  const profiler = new ApproxDuplicateProfiler();

  it("detects exact duplicate rows", () => {
    const data = new TabularData([
      { name: "Acme", city: "NYC" },
      { name: "Beta", city: "LA" },
      { name: "Acme", city: "NYC" },
      { name: "Gamma", city: "SF" },
    ]);
    const findings = profiler.profile(data);
    expect(checks(findings).has("duplicate_rows")).toBe(true);
    const f = findings.find((x) => x.check === "duplicate_rows")!;
    expect(f.affectedRows).toBe(2);
    expect(f.column).toBe("__dataset__");
    expect(f.metadata["duplicate_groups"]).toBe(1);
  });

  it("detects near-duplicate rows (case/whitespace/punct only)", () => {
    const data = new TabularData([
      { name: "Acme, Inc.", city: "New York" },
      { name: "acme inc", city: "new york" },
      { name: "Beta LLC", city: "Boston" },
    ]);
    const findings = profiler.profile(data);
    expect(checks(findings).has("near_duplicate_rows")).toBe(true);
    expect(findings.find((x) => x.check === "near_duplicate_rows")!.affectedRows).toBe(2);
  });

  it("does NOT also count exact dupes as near-dupes", () => {
    const data = new TabularData([
      { name: "Acme", city: "NYC" },
      { name: "Acme", city: "NYC" },
      { name: "Beta", city: "LA" },
    ]);
    const findings = profiler.profile(data);
    expect(checks(findings).has("duplicate_rows")).toBe(true);
    expect(checks(findings).has("near_duplicate_rows")).toBe(false);
  });

  it("is silent on clean data and trivial frames", () => {
    expect(profiler.profile(new TabularData([{ id: 1, name: "a" }, { id: 2, name: "b" }]))).toEqual([]);
    expect(profiler.profile(new TabularData([{ a: 1 }]))).toEqual([]);
    expect(profiler.profile(new TabularData([]))).toEqual([]);
  });
});
```

- [ ] **Step 2: Run it to confirm it fails** — `npx vitest run tests/unit/relations/approx-duplicate.test.ts`. Expected: FAIL, cannot find module.

- [ ] **Step 3: Implement** — `src/core/relations/approx-duplicate.ts`:

```ts
/**
 * Approximate / exact duplicate-row detection (cross-column relation).
 * Port of goldencheck/relations/approx_duplicate.py (pure-Polars → pure-JS;
 * no native kernel by design).
 *
 * - duplicate_rows: byte-identical rows.
 * - near_duplicate_rows: rows identical after lowercasing, collapsing
 *   whitespace, and dropping punctuation on string columns — and that have NO
 *   exact twin.
 */
import type { TabularData } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { RelationProfiler } from "../profilers/base.js";

const SEP = ""; // unit separator — won't appear in normal data

/** Per-row signature; string columns normalized, others stringified as-is. */
function signatures(data: TabularData, normalizeStrings: boolean): string[] {
  const stringCols = new Set(data.columns.filter((c) => data.dtype(c) === "string"));
  const colVals = data.columns.map((c) => data.column(c));
  const n = data.rowCount;
  const out: string[] = new Array(n);
  for (let i = 0; i < n; i++) {
    const parts: string[] = [];
    for (let c = 0; c < data.columns.length; c++) {
      const raw = colVals[c]![i];
      let s = raw === null ? "" : String(raw);
      if (normalizeStrings && stringCols.has(data.columns[c]!)) {
        s = s.toLowerCase().replace(/[^0-9a-z]+/g, " ").trim();
      }
      parts.push(s);
    }
    out[i] = parts.join(SEP);
  }
  return out;
}

export class ApproxDuplicateProfiler implements RelationProfiler {
  profile(data: TabularData): Finding[] {
    const nRows = data.rowCount;
    if (nRows < 2 || data.columns.length === 0) return [];

    const norm = signatures(data, true);
    const exact = signatures(data, false);

    const normCounts = new Map<string, number>();
    const exactCounts = new Map<string, number>();
    for (let i = 0; i < nRows; i++) {
      normCounts.set(norm[i]!, (normCounts.get(norm[i]!) ?? 0) + 1);
      exactCounts.set(exact[i]!, (exactCounts.get(exact[i]!) ?? 0) + 1);
    }

    const findings: Finding[] = [];

    // Exact duplicate rows.
    let exactDupRows = 0;
    const exactDupGroups = new Set<string>();
    for (let i = 0; i < nRows; i++) {
      if ((exactCounts.get(exact[i]!) ?? 0) >= 2) {
        exactDupRows++;
        exactDupGroups.add(exact[i]!);
      }
    }
    if (exactDupRows > 0) {
      const g = exactDupGroups.size;
      findings.push(
        makeFinding({
          severity: Severity.WARNING,
          column: "__dataset__",
          check: "duplicate_rows",
          message:
            `${exactDupRows} rows are exact duplicates ` +
            `(${g} distinct duplicated record${g !== 1 ? "s" : ""}).`,
          affectedRows: exactDupRows,
          suggestion:
            "De-duplicate before downstream processing, or confirm the " +
            "repetition is intentional (e.g. a denormalized fact table).",
          confidence: 0.7,
          metadata: { technique: "duplicate_rows", duplicate_groups: g },
        }),
      );
    }

    // Near-duplicates: share a normalized signature but have no exact twin.
    let nearDupRows = 0;
    const nearDupGroups = new Set<string>();
    for (let i = 0; i < nRows; i++) {
      if ((normCounts.get(norm[i]!) ?? 0) >= 2 && (exactCounts.get(exact[i]!) ?? 0) < 2) {
        nearDupRows++;
        nearDupGroups.add(norm[i]!);
      }
    }
    if (nearDupRows > 0) {
      const g = nearDupGroups.size;
      findings.push(
        makeFinding({
          severity: Severity.WARNING,
          column: "__dataset__",
          check: "near_duplicate_rows",
          message:
            `${nearDupRows} rows are near-duplicates — identical after ` +
            `lowercasing, collapsing whitespace, and removing punctuation ` +
            `(${g} group${g !== 1 ? "s" : ""}).`,
          affectedRows: nearDupRows,
          suggestion:
            "Standardize casing/whitespace/punctuation (or run an entity-" +
            "resolution pass) so these records reconcile to one.",
          confidence: 0.6,
          metadata: { technique: "near_duplicate_rows", near_duplicate_groups: g },
        }),
      );
    }

    return findings;
  }
}
```

- [ ] **Step 4: Run it to confirm it passes** — `npx vitest run tests/unit/relations/approx-duplicate.test.ts`. Expected: PASS.

- [ ] **Step 5: Register** — `src/core/relations/index.ts`: add export + import; append `new ApproxDuplicateProfiler()` after `IdentitySafePkProfiler` (final ordering settles in Task 7; for now just append).

- [ ] **Step 6: Run relation suite** — `npx vitest run tests/unit/relations/approx-duplicate.test.ts`. Expected: PASS. (Defer the `RELATION_PROFILERS.length` assertion to Task 7.)

- [ ] **Step 7: Commit**

```bash
git add src/core/relations/approx-duplicate.ts src/core/relations/index.ts \
  tests/unit/relations/approx-duplicate.test.ts
git commit -m "feat(ts): port approx-duplicate relation (exact + near-dup rows) (#855)"
```

---

## Task 5: Functional-dependency relation

Port of `functional_dependency.py`. Strict single-column FDs (`det -> dep` iff `nUniqueTuple([det,dep]) === nUnique(det)`), merged by determinant. INFO. Guards: ≥50 rows, ≥2 supported candidate columns.

**Files:**
- Create: `src/core/relations/functional-dependency.ts`
- Modify: `src/core/relations/index.ts`
- Test: `tests/unit/relations/functional-dependency.test.ts`

- [ ] **Step 1: Write the failing test** — `tests/unit/relations/functional-dependency.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { TabularData } from "../../../src/core/data.js";
import { Severity } from "../../../src/core/types.js";
import { FunctionalDependencyProfiler } from "../../../src/core/relations/functional-dependency.js";

function lookupData(n = 120): TabularData {
  const zipToCity: Record<number, number> = { 0: 0, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4 };
  return new TabularData(
    Array.from({ length: n }, (_, i) => {
      const zip = i % 6;
      return { zip, city: zipToCity[zip]!, amt: (i * 7) % 50 };
    }),
  );
}

describe("FunctionalDependencyProfiler", () => {
  const profiler = new FunctionalDependencyProfiler();

  it("discovers a strict FD (zip -> city)", () => {
    const findings = profiler.profile(lookupData());
    const f = findings.find((x) => x.metadata["determinant"] === "zip");
    expect(f).toBeDefined();
    expect(f!.check).toBe("functional_dependency");
    expect(f!.severity).toBe(Severity.INFO);
    expect((f!.metadata["dependents"] as string[])).toEqual(["city"]);
    expect(f!.column).toBe("zip");
    expect(f!.confidence).toBe(0.55);
  });

  it("reports nothing for independent columns", () => {
    const data = new TabularData(
      Array.from({ length: 120 }, (_, i) => ({ a: i % 5, b: (i * 3) % 7 })),
    );
    expect(profiler.profile(data)).toEqual([]);
  });

  it("requires minimum support", () => {
    expect(profiler.profile(lookupData(10))).toEqual([]);
  });
});
```

- [ ] **Step 2: Run it to confirm it fails** — `npx vitest run tests/unit/relations/functional-dependency.test.ts`. Expected: FAIL.

- [ ] **Step 3: Implement** — `src/core/relations/functional-dependency.ts`:

```ts
/**
 * Strict functional-dependency discovery (cross-column relation).
 * Port of goldencheck/relations/functional_dependency.py (the pure-Polars
 * fallback; the native kernel is Python-only and integer-exact-identical).
 *
 * det -> dep holds iff n_distinct(det, dep) === n_distinct(det). Skips unique
 * determinants and constant dependents. Merged by determinant; reported INFO.
 */
import type { TabularData, Dtype } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { RelationProfiler } from "../profilers/base.js";

const MIN_ROWS = 50;
const MAX_CANDIDATES = 12;
const MAX_FINDINGS = 10;
const SUPPORTED: ReadonlySet<Dtype> = new Set(["string", "integer", "boolean"]);

function selectCandidates(data: TabularData): string[] {
  const scored: Array<[number, string]> = [];
  for (const col of data.columns) {
    if (!SUPPORTED.has(data.dtype(col))) continue;
    const nu = data.nUnique(col);
    if (nu <= 1) continue;
    scored.push([nu, col]);
  }
  // Lowest-cardinality first (interesting determinants); stable on ties.
  scored.sort((a, b) => a[0] - b[0]);
  return scored.slice(0, MAX_CANDIDATES).map(([, c]) => c);
}

export class FunctionalDependencyProfiler implements RelationProfiler {
  profile(data: TabularData): Finding[] {
    const nRows = data.rowCount;
    if (nRows < MIN_ROWS || data.columns.length < 2) return [];

    const cols = selectCandidates(data);
    if (cols.length < 2) return [];

    const distinct = new Map<string, number>();
    for (const c of cols) distinct.set(c, data.nUnique(c));

    const pairs: Array<[number, number]> = [];
    for (let i = 0; i < cols.length; i++) {
      const det = cols[i]!;
      if (distinct.get(det) === nRows) continue; // unique determinant → trivial
      for (let j = 0; j < cols.length; j++) {
        if (i === j) continue;
        const dep = cols[j]!;
        if (distinct.get(dep)! <= 1) continue;
        if (data.nUniqueTuple([det, dep]) === distinct.get(det)) {
          pairs.push([i, j]);
        }
      }
    }
    if (pairs.length === 0) return [];

    // Merge by determinant (A->B and A->C become one finding).
    const detToDeps = new Map<string, string[]>();
    for (const [i, j] of pairs) {
      const det = cols[i]!;
      (detToDeps.get(det) ?? detToDeps.set(det, []).get(det)!).push(cols[j]!);
    }

    // Sort determinants by (deps count, name) descending — mirror Python.
    const dets = [...detToDeps.keys()].sort((a, b) => {
      const la = detToDeps.get(a)!.length;
      const lb = detToDeps.get(b)!.length;
      if (la !== lb) return lb - la;
      return a < b ? 1 : a > b ? -1 : 0; // reverse=True on the (len, det) tuple
    });

    const findings: Finding[] = [];
    for (const det of dets) {
      const deps = [...detToDeps.get(det)!].sort();
      const depsStr = deps.join(", ");
      const many = deps.length > 1;
      findings.push(
        makeFinding({
          severity: Severity.INFO,
          column: det,
          check: "functional_dependency",
          message:
            `Column '${det}' determines (${depsStr}) — each '${det}' value maps ` +
            `to a single value of ${many ? "these columns" : "this column"}, ` +
            `so ${many ? "they are" : "it is"} redundant given '${det}'.`,
          affectedRows: nRows,
          suggestion:
            "If this is a lookup relationship, consider normalizing " +
            `(${depsStr} into a table keyed by '${det}') to remove redundancy.`,
          confidence: 0.55,
          metadata: { technique: "functional_dependency", determinant: det, dependents: deps },
        }),
      );
      if (findings.length >= MAX_FINDINGS) break;
    }
    return findings;
  }
}
```

> Parity note on the determinant sort: Python does `sorted(det_to_deps, key=lambda d: (len(det_to_deps[d]), d), reverse=True)`. `reverse=True` reverses BOTH tuple components, so among equal dep-counts the determinant name sorts descending. The comparator above replicates that exactly.

- [ ] **Step 4: Run it to confirm it passes** — `npx vitest run tests/unit/relations/functional-dependency.test.ts`. Expected: PASS.

- [ ] **Step 5: Register** — `src/core/relations/index.ts`: add export + import; append `new FunctionalDependencyProfiler()` (ordering settled in Task 7).

- [ ] **Step 6: Commit**

```bash
git add src/core/relations/functional-dependency.ts src/core/relations/index.ts \
  tests/unit/relations/functional-dependency.test.ts
git commit -m "feat(ts): port functional-dependency relation (strict single-col FDs) (#855)"
```

---

## Task 6: Composite-key relation

Port of `composite_key.py`. Discovers minimal multi-column keys (size 2..3) when NO single-column key exists. INFO, anchored on the first key column. BFS with superset pruning.

**Files:**
- Create: `src/core/relations/composite-key.ts`
- Modify: `src/core/relations/index.ts`
- Test: `tests/unit/relations/composite-key.test.ts`

- [ ] **Step 1: Write the failing test** — `tests/unit/relations/composite-key.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { TabularData } from "../../../src/core/data.js";
import { CompositeKeyProfiler } from "../../../src/core/relations/composite-key.js";

function orderLines(): TabularData {
  // (order_id, line_no) is a composite key; neither column is unique alone.
  return new TabularData([
    { order_id: 1, line_no: 1, sku: "a", qty: 2 },
    { order_id: 1, line_no: 2, sku: "b", qty: 1 },
    { order_id: 1, line_no: 3, sku: "c", qty: 5 },
    { order_id: 2, line_no: 1, sku: "a", qty: 1 },
    { order_id: 2, line_no: 2, sku: "d", qty: 1 },
    { order_id: 3, line_no: 1, sku: "e", qty: 9 },
  ]);
}

describe("CompositeKeyProfiler", () => {
  const profiler = new CompositeKeyProfiler();

  it("discovers a composite key", () => {
    const findings = profiler.profile(orderLines());
    expect(findings.length).toBeGreaterThan(0);
    const keys = new Set(findings.map((f) => (f.metadata["key_columns"] as string[]).join("+")));
    expect(keys.has("order_id+line_no")).toBe(true);
    const f = findings.find((x) => (x.metadata["key_columns"] as string[]).join("+") === "order_id+line_no")!;
    expect(f.check).toBe("composite_key");
    expect(f.column).toBe("order_id"); // anchored on first key column
  });

  it("is silent when a single-column key exists", () => {
    const rows = orderLines().rows.map((r, i) => ({ ...r, pk: i }));
    expect(profiler.profile(new TabularData(rows))).toEqual([]);
  });

  it("is silent on trivial frames", () => {
    expect(profiler.profile(new TabularData([{ a: 1 }, { a: 2 }, { a: 3 }]))).toEqual([]);
    expect(profiler.profile(new TabularData([{ a: 1, b: 2 }]))).toEqual([]);
  });
});
```

- [ ] **Step 2: Run it to confirm it fails** — `npx vitest run tests/unit/relations/composite-key.test.ts`. Expected: FAIL.

- [ ] **Step 3: Implement** — `src/core/relations/composite-key.ts`:

```ts
/**
 * Composite-key discovery (cross-column relation).
 * Port of goldencheck/relations/composite_key.py (the pure-Polars fallback;
 * the native kernel is Python-only and parity-validated identical).
 *
 * Finds minimal column subsets (size 2..MAX_KEY_SIZE) whose tuples are all
 * distinct, but only when NO single-column key exists. Reported INFO.
 */
import type { TabularData, Dtype } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { RelationProfiler } from "../profilers/base.js";

const MAX_KEY_SIZE = 3;
const MAX_CANDIDATE_COLS = 12;
const MAX_REPORTED_KEYS = 3;
const SUPPORTED: ReadonlySet<Dtype> = new Set([
  "string", "integer", "float", "boolean",
]);

function hasSingleColumnKey(data: TabularData, nRows: number): boolean {
  for (const col of data.columns) {
    if (data.nullCount(col) === 0 && data.nUnique(col) === nRows) return true;
  }
  return false;
}

function selectCandidates(data: TabularData): string[] {
  const scored: Array<[number, string]> = [];
  for (const col of data.columns) {
    if (!SUPPORTED.has(data.dtype(col))) continue;
    const nu = data.nUnique(col);
    if (nu <= 1) continue;
    scored.push([nu, col]);
  }
  // Highest cardinality first — most likely to complete a key; stable on ties.
  scored.sort((a, b) => b[0] - a[0]);
  return scored.slice(0, MAX_CANDIDATE_COLS).map(([, c]) => c);
}

/** BFS mirror of goldencheck_core::composite_key_search. */
function search(
  data: TabularData,
  candidates: string[],
  nRows: number,
  maxSize: number,
): number[][] {
  const idxs = candidates.map((_, i) => i);
  const found: number[][] = [];
  const cap = Math.min(maxSize, idxs.length);
  let frontier: number[][] = idxs.map((i) => [i]);
  for (let size = 2; size <= cap; size++) {
    const next: number[][] = [];
    for (const base of frontier) {
      const last = base[base.length - 1]!;
      for (const c of idxs) {
        if (c <= last) continue;
        const subset = [...base, c];
        // Prune supersets of an already-found key.
        if (found.some((k) => k.every((x) => subset.includes(x)))) continue;
        const cols = subset.map((j) => candidates[j]!);
        if (data.nUniqueTuple(cols) === nRows) found.push(subset);
        else next.push(subset);
      }
    }
    if (next.length === 0) break;
    frontier = next;
  }
  return found;
}

export class CompositeKeyProfiler implements RelationProfiler {
  profile(data: TabularData): Finding[] {
    const nRows = data.rowCount;
    if (nRows < 2 || data.columns.length < 2) return [];
    if (hasSingleColumnKey(data, nRows)) return [];

    const candidates = selectCandidates(data);
    if (candidates.length < 2) return [];

    const keysIdx = search(data, candidates, nRows, MAX_KEY_SIZE);
    if (keysIdx.length === 0) return [];

    // Smallest keys first, then deterministic (lexicographic by column names).
    const keys = keysIdx.map((idxs) => idxs.map((i) => candidates[i]!));
    keys.sort((a, b) => {
      if (a.length !== b.length) return a.length - b.length;
      const len = Math.min(a.length, b.length);
      for (let k = 0; k < len; k++) {
        if (a[k] !== b[k]) return a[k]! < b[k]! ? -1 : 1;
      }
      return 0;
    });

    const findings: Finding[] = [];
    for (const key of keys.slice(0, MAX_REPORTED_KEYS)) {
      const colsStr = key.join(", ");
      findings.push(
        makeFinding({
          severity: Severity.INFO,
          column: key[0]!, // anchor on first key column
          check: "composite_key",
          message:
            `Columns (${colsStr}) form a composite key — together they ` +
            `uniquely identify every row, and no single column does.`,
          affectedRows: nRows,
          suggestion:
            "Use this column set as the natural join/dedup key, or add a " +
            "stable single-column surrogate key (UUID / autoincrement).",
          confidence: 0.6,
          metadata: { technique: "composite_key", key_columns: key },
        }),
      );
    }
    return findings;
  }
}
```

> Parity note: Polars `keys.sort(key=lambda k: (len(k), k))` compares the column-name lists lexicographically as a secondary key. The comparator above matches. The BFS subset ordering / candidate cardinality sort determine WHICH minimal keys are found and their order — keep `selectCandidates` cardinality-descending with stable ties (JS `Array.sort` is stable) to match Python's `scored.sort(..., reverse=True)`.

- [ ] **Step 4: Run it to confirm it passes** — `npx vitest run tests/unit/relations/composite-key.test.ts`. Expected: PASS.

- [ ] **Step 5: Register** — `src/core/relations/index.ts`: add export + import; append `new CompositeKeyProfiler()` (ordering settled in Task 7).

- [ ] **Step 6: Commit**

```bash
git add src/core/relations/composite-key.ts src/core/relations/index.ts \
  tests/unit/relations/composite-key.test.ts
git commit -m "feat(ts): port composite-key relation (minimal multi-col key discovery) (#855)"
```

---

## Task 7: Approx-FD relation + finalize relation registry ordering

Port of `approx_fd.py`. Near-strict FDs with violation-row surfacing (`fd_violation`, WARNING). First-seen interning, mode tie-break, avg-group-size guard (≥3). Then set the final `RELATION_PROFILERS` order to match Python and fix the count assertion.

**Files:**
- Create: `src/core/relations/approx-fd.ts`
- Modify: `src/core/relations/index.ts`
- Test: `tests/unit/relations/approx-fd.test.ts`, `tests/unit/relations/all-relations.test.ts`

- [ ] **Step 1: Write the failing test** — `tests/unit/relations/approx-fd.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { TabularData } from "../../../src/core/data.js";
import { Severity } from "../../../src/core/types.js";
import { ApproximateFDProfiler } from "../../../src/core/relations/approx-fd.js";

function nearFdData(n = 300): TabularData {
  const zipToCity = (z: number) => `city_${z}`;
  const rows = Array.from({ length: n }, (_, i) => {
    const zip = i % 10;
    return { zip, city: zipToCity(zip), amt: (i * 13) % 97 };
  });
  for (const bad of [7 % n, 50 % n, 123 % n]) rows[bad]!.city = "WRONGCITY";
  return new TabularData(rows);
}

describe("ApproximateFDProfiler", () => {
  const profiler = new ApproximateFDProfiler();

  it("surfaces near-FD violations (zip -> city, 3 typos)", () => {
    const findings = profiler.profile(nearFdData());
    const f = findings.find(
      (x) => x.metadata["determinant"] === "zip" && x.metadata["dependent"] === "city",
    );
    expect(f).toBeDefined();
    expect(f!.check).toBe("fd_violation");
    expect(f!.severity).toBe(Severity.WARNING);
    expect(f!.confidence).toBe(0.7);
    expect(f!.metadata["violation_count"]).toBe(3);
    expect(f!.affectedRows).toBe(3);
    expect(f!.metadata["fd_confidence"] as number).toBeGreaterThanOrEqual(0.95);
  });

  it("is silent on a perfect (strict) FD — that's the strict profiler's job", () => {
    const rows = Array.from({ length: 300 }, (_, i) => {
      const zip = i % 10;
      return { zip, city: `c${zip}` };
    });
    expect(profiler.profile(new TabularData(rows))).toEqual([]);
  });

  it("guards against a near-unique determinant", () => {
    const data = new TabularData(
      Array.from({ length: 300 }, (_, i) => ({ id: i, grp: i % 4 })),
    );
    const findings = profiler.profile(data);
    expect(findings.every((f) => f.metadata["determinant"] !== "id")).toBe(true);
  });

  it("requires minimum support", () => {
    expect(profiler.profile(nearFdData(40))).toEqual([]);
  });
});
```

- [ ] **Step 2: Run it to confirm it fails** — `npx vitest run tests/unit/relations/approx-fd.test.ts`. Expected: FAIL.

- [ ] **Step 3: Implement** — `src/core/relations/approx-fd.ts`:

```ts
/**
 * Approximate functional-dependency VIOLATION detection (cross-column relation).
 * Port of goldencheck/relations/approx_fd.py (the pure-Python fallback; the
 * native kernel is Python-only and produces identical violation sets).
 *
 * Surfaces near-strict FDs and the ROWS that break them: zip -> city holds
 * 99.7%, and the 0.3% are likely data-entry errors. WARNING.
 */
import type { TabularData, Dtype, ColumnValue } from "../data.js";
import { isNullish } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { RelationProfiler } from "../profilers/base.js";

const MIN_ROWS = 100;
const MIN_CONFIDENCE = 0.95;
const MIN_AVG_GROUP = 3;
const MAX_CANDIDATES = 12;
const MAX_FINDINGS = 8;
const SUPPORTED: ReadonlySet<Dtype> = new Set(["string", "integer", "boolean"]);

function selectCandidates(data: TabularData): string[] {
  const scored: Array<[number, string]> = [];
  for (const col of data.columns) {
    if (!SUPPORTED.has(data.dtype(col))) continue;
    const nu = data.nUnique(col);
    if (nu <= 1) continue;
    scored.push([nu, col]);
  }
  scored.sort((a, b) => a[0] - b[0]); // low-cardinality first (likely determinants)
  return scored.slice(0, MAX_CANDIDATES).map(([, c]) => c);
}

/** First-seen interning matching the native shim: null -> 0, values -> 1,2,… */
function intern(values: readonly ColumnValue[]): number[] {
  const ids: number[] = new Array(values.length);
  const seen = new Map<ColumnValue, number>();
  let nxt = 1;
  for (let r = 0; r < values.length; r++) {
    const v = values[r]!;
    if (isNullish(v)) {
      ids[r] = 0;
      continue;
    }
    let id = seen.get(v);
    if (id === undefined) {
      id = nxt++;
      seen.set(v, id);
    }
    ids[r] = id;
  }
  return ids;
}

/** Per determinant-group mode dependent, smallest-id tie-break. */
function groupModes(det: number[], dep: number[]): Map<number, number> {
  const counts = new Map<number, Map<number, number>>();
  for (let r = 0; r < det.length; r++) {
    const d = det[r]!;
    const p = dep[r]!;
    let inner = counts.get(d);
    if (!inner) {
      inner = new Map();
      counts.set(d, inner);
    }
    inner.set(p, (inner.get(p) ?? 0) + 1);
  }
  const modes = new Map<number, number>();
  for (const [d, depCounts] of counts) {
    let bestId = -1;
    let bestCnt = -1;
    for (const [pid, c] of depCounts) {
      if (c > bestCnt || (c === bestCnt && (bestId === -1 || pid < bestId))) {
        bestCnt = c;
        bestId = pid;
      }
    }
    modes.set(d, bestId);
  }
  return modes;
}

function violationRows(det: number[], dep: number[]): number[] {
  const modes = groupModes(det, dep);
  const out: number[] = [];
  for (let r = 0; r < det.length; r++) {
    if (modes.get(det[r]!) !== dep[r]!) out.push(r);
  }
  return out;
}

export class ApproximateFDProfiler implements RelationProfiler {
  profile(data: TabularData): Finding[] {
    const nRows = data.rowCount;
    if (nRows < MIN_ROWS || data.columns.length < 2) return [];
    const cols = selectCandidates(data);
    if (cols.length < 2) return [];

    const colsIds = cols.map((c) => intern(data.column(c)));
    const distinct = colsIds.map((c) => new Set(c).size);

    // Discover (det, dep, violationCount) triples above the confidence floor.
    const triples: Array<[number, number, number]> = [];
    for (let i = 0; i < colsIds.length; i++) {
      if (distinct[i] === 0 || distinct[i]! * MIN_AVG_GROUP > nRows) continue;
      for (let j = 0; j < colsIds.length; j++) {
        if (i === j || distinct[j]! <= 1) continue;
        const viol = violationRows(colsIds[i]!, colsIds[j]!).length;
        if (viol === 0) continue;
        if (1.0 - viol / nRows >= MIN_CONFIDENCE) triples.push([i, j, viol]);
      }
    }
    if (triples.length === 0) return [];

    triples.sort((a, b) => a[2] - b[2]); // fewest violations (highest conf) first

    const findings: Finding[] = [];
    for (const [i, j, viol] of triples.slice(0, MAX_FINDINGS)) {
      const det = cols[i]!;
      const dep = cols[j]!;
      const confidence = 1.0 - viol / nRows;
      const rows = violationRows(colsIds[i]!, colsIds[j]!).slice(0, 5);
      const detVals = data.column(det);
      const depVals = data.column(dep);
      const samples = rows.map(
        (r) => `${det}=${JSON.stringify(detVals[r])} has ${dep}=${JSON.stringify(depVals[r])}`,
      );
      findings.push(
        makeFinding({
          severity: Severity.WARNING,
          column: dep,
          check: "fd_violation",
          message:
            `'${dep}' is almost always determined by '${det}' ` +
            `(${(confidence * 100).toFixed(1)}% of rows); ${viol} row(s) break the pattern — ` +
            `likely data-entry errors.`,
          affectedRows: viol,
          sampleValues: samples,
          suggestion:
            `Review the ${viol} row(s) where '${dep}' disagrees with the value ` +
            `'${det}' usually maps to; correct or confirm them.`,
          confidence: 0.7,
          metadata: {
            technique: "fd_violation",
            determinant: det,
            dependent: dep,
            fd_confidence: Math.round(confidence * 1e6) / 1e6,
            violation_count: viol,
          },
        }),
      );
    }
    return findings;
  }
}
```

> Parity note: Python builds `samples` with `f"{det}={df[det][r]!r}"` (Python `repr`). The TS `JSON.stringify` form differs textually — `sample_values` are NOT asserted in parity goldens, so this is fine. What IS asserted: `affectedRows` (= viol) and `confidence` (0.7). `fd_confidence` metadata uses 6-decimal rounding to mirror Python `round(confidence, 6)`.

- [ ] **Step 4: Run it to confirm it passes** — `npx vitest run tests/unit/relations/approx-fd.test.ts`. Expected: PASS.

- [ ] **Step 5: Finalize the relation registry** — rewrite the `RELATION_PROFILERS` array in `src/core/relations/index.ts` to the exact Python order (after the 5 existing entries): `CompositeKeyProfiler`, `ApproxDuplicateProfiler`, `FunctionalDependencyProfiler`, `ApproximateFDProfiler`. Ensure all four are exported + imported. Final array:

```ts
export const RELATION_PROFILERS: readonly RelationProfiler[] = [
  new TemporalOrderProfiler(),
  new NullCorrelationProfiler(),
  new NumericCrossColumnProfiler(),
  new AgeValidationProfiler(),
  new IdentitySafePkProfiler(),
  // Discover minimal composite keys when no single-column key exists.
  new CompositeKeyProfiler(),
  // Exact + near-duplicate (normalized) row detection.
  new ApproxDuplicateProfiler(),
  // Discover strict single-column functional dependencies.
  new FunctionalDependencyProfiler(),
  // Surface rows that BREAK a near-strict FD (likely data-entry errors).
  new ApproximateFDProfiler(),
];
```

- [ ] **Step 6: Update the registry count assertion** — `tests/unit/relations/all-relations.test.ts`: change `expect(RELATION_PROFILERS.length).toBe(5)` → `toBe(9)` and update the comment to list all nine.

- [ ] **Step 7: Run the full relation + profiler suites** — `npx vitest run tests/unit/relations/ tests/unit/profilers/`. Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/core/relations/approx-fd.ts src/core/relations/index.ts \
  tests/unit/relations/approx-fd.test.ts tests/unit/relations/all-relations.test.ts
git commit -m "feat(ts): port approx-fd relation + finalize relation registry order (#855)"
```

---

## Task 8: `validate` MCP tool

Port `_tool_validate` from `goldencheck/mcp/server.py` into the TS MCP server. Reuses the existing `validateData` (core) + `validateConfig` (config schema) + `yaml` (already a dep). Node-layer file/config I/O lives in the MCP server (`src/node/`), which is allowed.

> **Scope note:** issue #855 names only `validate` under "Missing MCP tool" (`install_domain` is mentioned parenthetically). `install_domain` downloads a community pack over the network and has no TS counterpart infrastructure — it is **out of scope** for this plan. Note it as a follow-up.

**Files:**
- Modify: `src/node/mcp/server.ts`
- Test: `tests/unit/mcp-validate.test.ts` (new), `tests/unit/mcp-agent-tools.test.ts` (count)

- [ ] **Step 1: Write the failing test** — `tests/unit/mcp-validate.test.ts`:

```ts
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { handleTool, TOOL_DEFINITIONS } from "../../src/node/mcp/server.js";

let dir: string;
let dataPath: string;
let configPath: string;

beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), "gc-validate-"));
  dataPath = join(dir, "data.csv");
  configPath = join(dir, "goldencheck.yml");
  // status has an out-of-enum value "bogus".
  writeFileSync(dataPath, "id,status\n1,active\n2,bogus\n3,active\n");
  writeFileSync(
    configPath,
    [
      "version: 1",
      "settings:",
      "  sample_size: 100000",
      "  severity_threshold: warning",
      "  fail_on: error",
      "columns:",
      "  status:",
      "    type: string",
      "    enum: [active, inactive]",
      "relations: []",
      "ignore: []",
      "",
    ].join("\n"),
  );
});

afterAll(() => rmSync(dir, { recursive: true, force: true }));

describe("validate MCP tool", () => {
  it("is registered (8 core + 10 agent = 18 tools)", () => {
    expect(TOOL_DEFINITIONS.length).toBe(18);
    expect(TOOL_DEFINITIONS.map((t) => t.name)).toContain("validate");
  });

  it("reports enum violations and fails the gate", () => {
    const r = handleTool("validate", { file_path: dataPath, config_path: configPath }) as Record<string, unknown>;
    expect(r["pass"]).toBe(false);
    expect(r["errors"]).toBeGreaterThanOrEqual(1);
    const findings = r["findings"] as Array<{ check: string }>;
    expect(findings.some((f) => f.check === "enum")).toBe(true);
  });

  it("returns an error for a missing file", () => {
    const r = handleTool("validate", { file_path: join(dir, "nope.csv"), config_path: configPath }) as Record<string, unknown>;
    expect(typeof r["error"]).toBe("string");
  });

  it("returns an error for a missing config", () => {
    const r = handleTool("validate", { file_path: dataPath, config_path: join(dir, "nope.yml") }) as Record<string, unknown>;
    expect(typeof r["error"]).toBe("string");
  });
});
```

> Confirm the YAML key names against `validateConfig` in `src/core/config/schema.ts` before finalizing the fixture — if that parser expects camelCase or different settings keys, match it (the goldencheck-types exception keeps some keys snake_case). Adjust the fixture, not the assertions.

- [ ] **Step 2: Run it to confirm it fails** — `npx vitest run tests/unit/mcp-validate.test.ts`. Expected: FAIL (tool count 17, no `validate`).

- [ ] **Step 3: Implement** — in `src/node/mcp/server.ts`:

  (a) Add the tool definition to `CORE_TOOL_DEFINITIONS` (after `scan`, mirroring Python order):

```ts
  {
    name: "validate",
    description:
      "Validate a data file against pinned rules in goldencheck.yml. " +
      "Returns validation findings (existence, required, unique, enum, range checks).",
    inputSchema: {
      type: "object" as const,
      properties: {
        file_path: { type: "string" as const, description: "Path to the data file" },
        config_path: {
          type: "string" as const,
          description: "Path to goldencheck.yml (default: ./goldencheck.yml)",
          default: "goldencheck.yml",
        },
      },
      required: ["file_path"],
    },
  },
```

  (b) Update the surface comment near `TOOL_DEFINITIONS`: "8 core tools + 10 agent tools".

  (c) Add a `case "validate": return toolValidate(args);` to the `handleTool` switch.

  (d) Implement the handler (place near `toolScan`):

```ts
function toolValidate(args: Record<string, unknown>): object {
  const filePath = args["file_path"] as string;
  const configPath = (args["config_path"] as string) ?? "goldencheck.yml";

  const { existsSync, readFileSync } = require("node:fs") as typeof import("node:fs");
  if (!existsSync(filePath)) return { error: `File not found: ${filePath}` };
  if (!existsSync(configPath)) {
    return { error: `No config found at ${configPath}. Run scan first.` };
  }

  const { validateConfig } = require("../../core/config/schema.js") as {
    validateConfig(raw: unknown): import("../../core/types.js").GoldenCheckConfig;
  };
  const { validateData } = require("../../core/engine/validator.js") as {
    validateData(d: ReturnType<typeof readFile>, c: import("../../core/types.js").GoldenCheckConfig): Finding[];
  };
  const yaml = require("yaml") as { parse(s: string): unknown };

  const config = validateConfig(yaml.parse(readFileSync(configPath, "utf-8")));
  const data = readFile(filePath);
  const findings = validateData(data, config);

  return {
    file: filePath,
    config: configPath,
    total_findings: findings.length,
    errors: findings.filter((f) => f.severity === Severity.ERROR).length,
    warnings: findings.filter((f) => f.severity === Severity.WARNING).length,
    pass: findings.every((f) => f.severity < Severity.ERROR),
    findings: serializeFindings(findings),
  };
}
```

> Use `require(...)` (not top-level `import`) for `node:fs`/`yaml`/the config+validator modules, matching how `cli.ts` and `a2a/server.ts` already lazy-load them — this keeps the dts bundler happy and `yaml` an optional peer.

  (e) Add the new check names to `toolListChecks()` for completeness (optional but mirrors Python `list_checks`): append entries for `fuzzy_duplicate_values`, `future_dated`, `stale_data`, `duplicate_rows`, `near_duplicate_rows`, `composite_key`, `functional_dependency`, `fd_violation`.

- [ ] **Step 4: Update the tool-count assertion** — `tests/unit/mcp-agent-tools.test.ts` line ~42-43: change the title to "(8 core + 10 agent = 18)" and `expect(TOOL_DEFINITIONS.length).toBe(18)`. Also add `expect(TOOL_DEFINITIONS.map((t) => t.name)).toContain("validate");`.

- [ ] **Step 5: Run it to confirm it passes** — `npx vitest run tests/unit/mcp-validate.test.ts tests/unit/mcp-agent-tools.test.ts`. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/node/mcp/server.ts tests/unit/mcp-validate.test.ts tests/unit/mcp-agent-tools.test.ts
git commit -m "feat(ts): add validate MCP tool (parity with Python core tools) (#855)"
```

---

## Task 9: Parity harness hardening + new goldens

Extend the golden generator to emit `affected_rows`, add CSV-survivable parity cases for the 5 round-trippable modules, regenerate + commit goldens, and harden `parity.test.ts` to assert `confidence` + `affectedRows` and to **fail** (not skip) on missing manifest/golden. Freshness is excluded (ground rule 2).

**Files:**
- Modify: `packages/python/goldencheck/scripts/gen_parity_goldens_js.py`
- Modify: `packages/typescript/goldencheck/tests/fixtures/parity_cases.json`
- Modify: `packages/typescript/goldencheck/tests/parity/parity.test.ts`
- Create/Regenerate: `packages/typescript/goldencheck/tests/fixtures/_goldens_js/*.json`

- [ ] **Step 1: Emit `affected_rows` in the golden generator** — in `scripts/gen_parity_goldens_js.py`, add `"affected_rows": f.affected_rows` to the per-finding dict in the `golden["findings"]` comprehension (alongside `severity`, `column`, `check`, `confidence`).

- [ ] **Step 2: Add parity cases** — append to `tests/fixtures/parity_cases.json` `cases` array. Design each fixture per the ground rules (string/int only, no nulls, isolated). Suggested cases (tune row data so exactly the intended module fires; keep collateral findings stable):

  - `fuzzy_values`: 120 rows, `state` cycling `["California","Californa","CALIFORNIA","Texas","New York"]`, plus a clean `region` column. Expect one `fuzzy_duplicate_values` on `state`.
  - `approx_duplicate_exact`: ~12 rows with 2 byte-identical (`name`,`city`) rows; rest distinct. Expect `duplicate_rows`.
  - `approx_fd`: 300 rows, `zip` (0–9), `city` = `city_{zip}` with 3 injected `WRONGCITY` typos, `amt` noise. Expect `fd_violation` zip→city, violation_count 3.
  - `functional_dependency`: 120 rows, `zip` (0–5), `city` = strict map, `amt` noise. Expect `functional_dependency` zip→city.
  - `composite_key`: ~60 rows where `(order_id, line_no)` is the minimal key and no single column is unique; columns `order_id`,`line_no`,`sku` (all string/int). Expect `composite_key`.

  Each case:
```json
{
  "name": "approx_fd",
  "description": "near-strict zip->city FD with injected violations",
  "input": { "kind": "records", "records": [ /* generated rows */ ] },
  "options": { "sampleSize": 100000, "domain": null }
}
```

> Generate the `records` arrays with a tiny throwaway script (Node or Python) and paste them in — do NOT hand-type 300 rows. Keep numeric columns as JSON numbers and categorical as JSON strings so both the Python CSV path and the TS `TabularData` agree on dtype.

- [ ] **Step 3: Regenerate goldens (Python)** — from `packages/python/goldencheck/`:

```bash
POLARS_SKIP_CPU_CHECK=1 python scripts/gen_parity_goldens_js.py
```

  This rewrites every golden in `tests/fixtures/_goldens_js/` (including `simple_mixed.json`) with the new `affected_rows` field. Confirm one new golden, e.g.:

```bash
cat tests/fixtures/_goldens_js/approx_fd.json
```
  Expected: contains a finding with `"check": "fd_violation"`, `"affected_rows": 3`.

> The generator runs the Python **fallback** (no native kernel built locally) — exactly what the TS port mirrors. If a teammate has the kernel installed, output is still byte-identical per the CLAUDE.md guarantee.

- [ ] **Step 4: Harden `parity.test.ts`** — rewrite the comparison + missing-golden handling:

  (a) Extend `GoldenOutput.findings` items to include `affected_rows: number` (keep `confidence`).

  (b) Change the manifest-missing guard from `it.skip(...)` to a failing test:
```ts
  if (!existsSync(MANIFEST_PATH)) {
    it("parity_cases.json must exist (run scripts/gen_parity_goldens_js.py)", () => {
      throw new Error(`Missing parity manifest at ${MANIFEST_PATH}`);
    });
    return;
  }
```

  (c) Change the per-case missing-golden early-`return` to an assertion:
```ts
      if (!existsSync(goldenPath)) {
        throw new Error(`Missing golden for "${testCase.name}" — run scripts/gen_parity_goldens_js.py`);
      }
```

  (d) Include `confidence` (rounded to 4) and `affectedRows` in both projected arrays, and sort by a key spanning all fields so multi-finding-per-column cases (fuzzy, approx-duplicate) compare order-independently:
```ts
      const round4 = (x: number) => Math.round(x * 1e4) / 1e4;
      const tsFindings = findings.map((f) => ({
        column: f.column,
        check: f.check,
        severity: f.severity === 3 ? "ERROR" : f.severity === 2 ? "WARNING" : "INFO",
        confidence: round4(f.confidence),
        affectedRows: f.affectedRows,
      }));
      const pyFindings = golden.findings.map((f) => ({
        column: f.column,
        check: f.check,
        severity: f.severity,
        confidence: round4(f.confidence),
        affectedRows: f.affected_rows,
      }));
      const sortKey = (f: { column: string; check: string; affectedRows: number }) =>
        `${f.column}|${f.check}|${f.affectedRows}`;
      tsFindings.sort((a, b) => sortKey(a).localeCompare(sortKey(b)));
      pyFindings.sort((a, b) => sortKey(a).localeCompare(sortKey(b)));
      expect(tsFindings).toEqual(pyFindings);
```

  (e) Add a top-of-file comment documenting that freshness is intentionally NOT in this harness (Polars CSV `try_parse_dates=False` → date columns read as Utf8 → freshness can't fire through CSV; covered by `tests/unit/profilers/freshness.test.ts`).

- [ ] **Step 5: Run the parity test** — from `packages/typescript/goldencheck/`:

```bash
npx vitest run tests/parity/parity.test.ts
```
  Expected: PASS for every case, including `simple_mixed` with the now-asserted `confidence`/`affectedRows`.

  **If a case fails:** diff the TS findings vs the golden.
  - If the divergence is in one of the **6 newly-ported modules**, fix the port (it's a real parity bug — that's the point of this harness).
  - If it's a pre-existing divergence in an **already-ported** profiler surfaced by a new fixture (e.g. `simple_mixed` or collateral findings), first try to neutralize it by reshaping the fixture to avoid that profiler; if it's a genuine latent gap unrelated to #855, **do not expand scope** — narrow the offending fixture and open a follow-up issue noting the gap. Record the decision in the commit message.

- [ ] **Step 6: Commit**

```bash
cd ../../..  # repo root, to stage both the python script and TS fixtures
git add packages/python/goldencheck/scripts/gen_parity_goldens_js.py \
  packages/typescript/goldencheck/tests/fixtures/parity_cases.json \
  packages/typescript/goldencheck/tests/fixtures/_goldens_js/ \
  packages/typescript/goldencheck/tests/parity/parity.test.ts
git commit -m "test(ts): harden parity harness (confidence+rows, fail-on-missing) + module goldens (#855)"
```

---

## Task 10: Full verification, typecheck, build, docs

- [ ] **Step 1: Typecheck** — `cd packages/typescript/goldencheck && npm run typecheck`. Expected: clean. Fix any `noUncheckedIndexedAccess` / strictness issues in the new files (the port code uses `!` non-null assertions consistent with the existing codebase).

- [ ] **Step 2: Full test suite** — `npm run test`. Expected: all green, including the bumped registry counts (12 profilers, 9 relations, 18 MCP tools) and parity.

- [ ] **Step 3: Build** — `npm run build`. Expected: tsup emits ESM+CJS+d.ts with no errors. (Confirms the MCP server's lazy `require` pattern didn't break the dts bundler.)

- [ ] **Step 4: Docs** — update:
  - `packages/python/goldencheck/CLAUDE.md` "TypeScript Port" section: note the port now includes freshness + fuzzy-values profilers, approx-duplicate / approx-fd / composite-key / functional-dependency relations, and the `validate` MCP tool (8 core + 10 agent = 18); parity harness now asserts confidence + affected_rows and fails on missing goldens; freshness is unit-test-only (CSV harness can't represent date dtype).
  - `packages/typescript/goldencheck/` changelog/CHANGELOG (match the existing format) with a `### Added` entry referencing #855.

- [ ] **Step 5: Commit docs**

```bash
git add packages/python/goldencheck/CLAUDE.md packages/typescript/goldencheck/CHANGELOG*.md
git commit -m "docs(ts): record goldencheck #855 parity ports (profilers/relations/validate)"
```

- [ ] **Step 6: Final verification statement** — confirm with evidence (paste the `npm run test` summary line showing pass counts and the parity test count) before declaring the issue complete. Per superpowers:verification-before-completion, do not claim done without the green output in hand.

---

## Out of Scope (explicit)

- **Native kernels** — the 5 deep-profiling Rust kernels (Benford, composite-key interning, FD early-exit, approx-FD, fuzzy Levenshtein blocking) are Python-only by design (#855). No TS native path.
- **`install_domain` MCP tool** — needs a community-pack download/registry not present in the TS port. Follow-up only.
- **Freshness in the CSV golden harness** — structurally impossible (Polars CSV reads dates as Utf8); covered by unit tests.
- **Pre-existing parity gaps in already-ported profilers** — if surfaced by new fixtures, narrow the fixture + file a follow-up; do not fix here.

## Definition of Done

- [ ] 6 new modules implemented, each with passing unit tests mirroring the Python sibling tests.
- [ ] `COLUMN_PROFILERS` = 12, `RELATION_PROFILERS` = 9, MCP `TOOL_DEFINITIONS` = 18, all assertions updated.
- [ ] `validate` MCP tool returns the Python-shape payload and gates on ERROR severity.
- [ ] Parity harness asserts `confidence` + `affectedRows`, fails on missing manifest/golden, and is green for all cases (5 new + `simple_mixed`).
- [ ] `npm run typecheck`, `npm run test`, `npm run build` all green.
- [ ] Docs updated. Branch ready for PR against `main` referencing #855.
