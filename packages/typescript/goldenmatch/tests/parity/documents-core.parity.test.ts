/**
 * Cross-surface parity: the TS documents-core wasm kernels vs the SHARED corpus
 * (`documents_corpus.jsonl`, Python-authored, asserted ==native in the Python
 * parity lane, copied into the TS fixtures by `scripts/build_documents_wasm.mjs`).
 * Green here == one kernel set, three surfaces (Python / Rust / TS), zero drift.
 *
 * Per-kernel comparison mirrors the Python oracle `_run_native` in
 * `test_documents_parity.py`:
 *  - schema:                JSON.parse(kernel) deep-equals expected.ok (object)
 *  - parse / prompt_*:      the RAW kernel string equals expected.ok (string)
 *  - normalize:             reshape the kernel's {values,confidence} object into
 *                           ORDERED [col,val] pairs (schema field order) BEFORE
 *                           comparing — expected.ok is ordered pairs, NOT the raw
 *                           object. (The #1 trap; see the design doc.)
 *  - error rows:            the kernel THROWS; if expected.substring is set, the
 *                           thrown message includes it.
 *
 * NO static skip-gate: the committed bindings + CI's fresh rebuild are BOTH
 * expected to init under vitest (autoconfig/suggest parity prove wasm loads
 * here). A kernel that won't load must be RED, not silently skipped — this is
 * the byte-parity proof, so it fails loud (the fingerprint-wasm precedent).
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import {
  validateSchema,
  parseMessageText,
  extractInstruction,
  suggestPrompt,
  normalizeRecord,
  type TargetSchema,
} from "../../src/core/documentsWasm.js";

interface Row {
  kernel: "schema" | "parse" | "prompt_extract" | "prompt_suggest" | "normalize";
  input: any;
  expected: { ok?: unknown; error?: boolean; substring?: string };
}

const here = dirname(fileURLToPath(import.meta.url));
const rows: Row[] = readFileSync(
  resolve(here, "fixtures/documents/documents_corpus.jsonl"),
  "utf8",
)
  .split("\n")
  .map((l) => l.trim())
  .filter((l) => l.length > 0)
  .map((l) => JSON.parse(l) as Row);

// Reshape the normalize kernel's object output into ordered [col,val] pairs,
// keyed to the schema's field order — exactly as the Python oracle does.
function normalizeToPairs(input: any): {
  values: [string, unknown][];
  confidence: [string, number][];
} {
  const cols: string[] = input.schema.fields.map((f: any) => f.name);
  const out = normalizeRecord(input.values, input.confidence, input.schema as TargetSchema);
  return {
    values: cols.map((c) => [c, out.values[c] ?? null]),
    confidence: cols.map((c) => [c, Number(out.confidence[c] ?? 0)]),
  };
}

// Run one kernel for the SUCCESS path; returns a value shaped like expected.ok.
function runOk(row: Row): unknown {
  switch (row.kernel) {
    case "schema":
      return validateSchema(row.input as TargetSchema);
    case "parse":
      return parseMessageText(row.input);
    case "prompt_extract":
      return extractInstruction(row.input as TargetSchema);
    case "prompt_suggest":
      return suggestPrompt();
    case "normalize":
      return normalizeToPairs(row.input);
  }
}

describe("documents-core wasm parity (TS == shared corpus)", () => {
  it("loaded a non-trivial corpus", () => {
    expect(rows.length).toBeGreaterThanOrEqual(20);
  });

  rows.forEach((row, i) => {
    it(`${row.kernel}[${i}]: ${row.expected.error ? "throws" : "matches expected.ok"}`, () => {
      if (row.expected.error) {
        expect(() => runOk(row)).toThrow();
        if (row.expected.substring) {
          expect(() => runOk(row)).toThrow(new RegExp(row.expected.substring));
        }
        return;
      }
      expect(runOk(row)).toEqual(row.expected.ok);
    });
  });
});
