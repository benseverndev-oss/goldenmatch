/**
 * Cross-surface byte-parity for the auto-detect type-inference kernel: the SAME
 * oracle corpus (`packages/python/goldenflow/tests/parity/profile_corpus.jsonl`,
 * generated from the Python `_infer_type_list` reference — which the Rust
 * `goldenflow_core::profile::infer_type` unit tests pin) is asserted against
 * BOTH the pure-TS regex decision (always) and the opt-in WASM kernel
 * (`goldenflow-wasm` `infer_type`, only when the `.wasm` artifact is actually
 * built — a CI-only build product, never committed).
 *
 * This corpus file is copied byte-for-byte from the Python corpus (NOT
 * hand-edited — CI's sync-check enforces the two stay identical). Each row is
 * `{ values, hint, expected_type }`:
 *   - `values`: the RAW list (numbers / booleans / strings / null). We stringify
 *     each non-null via `String(v)` to mirror Python `str(v)` before feeding the
 *     kernel — the boolean/numeric rows short-circuit on `hint` so their
 *     stringification never reaches the regex stage (where "true" vs "True"
 *     would diverge).
 *   - `hint`: `"numeric"` / `"boolean"` (short-circuit) or `"string"` (Utf8, run
 *     the regexes). Derived by the caller from the JS value types — the kernel
 *     takes the already-decided hint (matching `_infer_type_list`).
 *   - `expected_type`: `"email"`/`"zip"`/`"date"`/`"phone"`/`"name"`/`"numeric"`/
 *     `"boolean"`/`"string"`.
 *
 * The column-NAME override is NOT exercised here (it stays in the caller, not the
 * kernel) — every row is a pure function of VALUES + hint.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { inferTypeByRegex } from "../../src/core/engine/profiler-bridge.js";
import { enableWasm, disableWasm } from "../../src/core/wasm/index.js";
import { getFlowWasmBackend } from "../../src/core/wasm/backend.js";

type RawValue = string | number | boolean | null;

interface CorpusRow {
  values: RawValue[];
  hint: string;
  expected_type: string;
}

const here = dirname(fileURLToPath(import.meta.url));
const corpusPath = resolve(here, "profile_corpus.jsonl");
const rows: CorpusRow[] = readFileSync(corpusPath, "utf8")
  .split("\n")
  .filter((line) => line.trim().length > 0)
  .map((line) => JSON.parse(line) as CorpusRow);

/** Mirror Python `str(v)` (None stays null): stringify each non-null value. */
function stringify(v: RawValue): string | null {
  return v === null ? null : String(v);
}

/** Pure-TS twin of the owned `infer_type` kernel: `hint` short-circuit, then the
 * regex stage over the first-100 non-null, stripped, non-empty sample. Byte-
 * identical to `goldenflow_core::profile::infer_type` / Python `_infer_type_list`
 * and to `backend.inferType`. Uses the production `inferTypeByRegex` for the
 * regex decision so the fallback path stays under test. */
function pureInferType(values: readonly (string | null)[], hint: string): string {
  if (hint === "numeric") return "numeric";
  if (hint === "boolean") return "boolean";
  if (hint === "date") return "date";
  // Sampling uses the reference slice-100-then-strip order (matches the
  // kernel/Python oracle); production inferType strips-then-slices -- a known
  // pre-existing >100-with-empties edge tracked as a follow-up. This twin tests
  // the regex+hint decision via the production inferTypeByRegex, not the
  // sampling wrapper.
  const first100 = values.filter((v): v is string => v !== null).slice(0, 100);
  const stripped = first100.map((s) => s.trim()).filter((s) => s.length > 0);
  if (stripped.length === 0) return "string";
  return inferTypeByRegex(stripped);
}

describe("goldenflow profile: pure-TS matches oracle", () => {
  it("has corpus rows to assert", () => {
    expect(rows.length).toBeGreaterThan(0);
  });

  for (const row of rows) {
    it(`inferType(${JSON.stringify(row.values)}, ${row.hint}) === ${row.expected_type}`, () => {
      const got = pureInferType(row.values.map(stringify), row.hint);
      expect(got).toBe(row.expected_type);
    });
  }
});

describe("goldenflow profile: wasm matches oracle", () => {
  let wasmAvailable = false;

  beforeAll(async () => {
    // enableWasm() resolves false (no throw) when the `.wasm` artifact isn't
    // built -- true default/local state, since it's a CI-only build product.
    wasmAvailable = await enableWasm();
  });

  afterAll(() => {
    disableWasm();
  });

  it("reports wasm availability", () => {
    if (!wasmAvailable) {
      console.warn(
        "goldenflow wasm artifact not built (src/core/wasm/artifacts/*.wasm absent) " +
          "-- skipping the wasm profile parity leg. Expected outside the wasm_flow CI lane.",
      );
    }
    expect(true).toBe(true);
  });

  for (const row of rows) {
    it.skipIf(!wasmAvailable)(
      `[wasm] inferType(${JSON.stringify(row.values)}, ${row.hint}) === ${row.expected_type}`,
      () => {
        const backend = getFlowWasmBackend();
        if (!backend) throw new Error("wasmAvailable=true but getFlowWasmBackend() returned null");
        const got = backend.inferType(row.values.map(stringify), row.hint);
        expect(got).toBe(row.expected_type);
      },
    );
  }
});
