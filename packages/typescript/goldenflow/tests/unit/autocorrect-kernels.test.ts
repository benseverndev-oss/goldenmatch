/**
 * Pinned-vector parity for the data-dependent FUZZY autocorrect kernel (Wave D
 * autocorrect): `fuzzRatioTs` (rapidfuzz `fuzz.ratio`) + `categoryAutoCorrect`
 * (the whole value_counts -> build_canonical_map -> strip+apply pipeline).
 *
 * These don't fit the shared string->scalar corpus: `fuzz_ratio` is two-input
 * -> float, and `category_auto_correct` is column -> column (data-dependent).
 * They're asserted here with the SAME pinned vectors as the Python
 * `tests/transforms/test_autocorrect_kernels.py`, against the goldenflow-core
 * Rust kernel's values.
 *
 * The pure-TS leg always runs; the WASM leg runs only when the `.wasm` artifact
 * has been built (a CI-only build product, never committed) -- the skip-guarded
 * pattern from the text-kernel harness.
 */
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { fuzzRatioTs } from "../../src/core/transforms/auto-correct.js";
import { getTransform } from "../../src/core/transforms/registry.js";
import type { ColumnValue } from "../../src/core/types.js";
import "../../src/core/transforms/auto-correct.js";
import { enableWasm, disableWasm } from "../../src/core/wasm/index.js";
import { getFlowWasmBackend, type FlowWasmBackend } from "../../src/core/wasm/backend.js";

// (a, b, expected) rows — pinned vs Python rapidfuzz.fuzz.ratio.
const FUZZ: Array<[string, string, number]> = [
  ["active", "actve", 90.9090909090909],
  ["aaa", "aa", 80],
  ["kitten", "sitting", 61.53846153846154],
  ["abc", "", 0],
  ["", "", 100],
  ["abc", "abc", 100],
];

// The scenario column: 50×"active" + 10×"Active" + 5×"ACTIVE" + 2×"actve" +
// 1×"banana" + 3×null. Case variants + a fuzzy typo all collapse to "active";
// "banana" is below threshold; nulls stay null. Tie-free counts.
function scenarioColumn(): ColumnValue[] {
  const out: ColumnValue[] = [];
  for (let i = 0; i < 50; i++) out.push("active");
  for (let i = 0; i < 10; i++) out.push("Active");
  for (let i = 0; i < 5; i++) out.push("ACTIVE");
  for (let i = 0; i < 2; i++) out.push("actve");
  out.push("banana");
  for (let i = 0; i < 3; i++) out.push(null);
  return out;
}

function expectScenario(result: ColumnValue[]): void {
  // All 67 active-variants -> "active".
  for (let i = 0; i < 67; i++) expect(result[i]).toBe("active");
  // "banana" unchanged.
  expect(result[67]).toBe("banana");
  // nulls stay null.
  expect(result[68]).toBeNull();
  expect(result[69]).toBeNull();
  expect(result[70]).toBeNull();
}

describe("goldenflow autocorrect kernels: pure-TS matches pinned vectors", () => {
  it("fuzzRatioTs", () => {
    for (const [a, b, expected] of FUZZ) {
      expect(fuzzRatioTs(a, b)).toBeCloseTo(expected, 9);
    }
  });

  it("category_auto_correct scenario", () => {
    const transform = getTransform("category_auto_correct");
    if (!transform) throw new Error("category_auto_correct not registered");
    const result = transform.func(scenarioColumn()) as ColumnValue[];
    expectScenario(result);
  });
});

describe("goldenflow autocorrect kernels: wasm matches pinned vectors", () => {
  let wasmAvailable = false;

  beforeAll(async () => {
    // Resolves false (no throw) when the `.wasm` artifact isn't built -- the
    // true default/local state (CI-only build product; never committed).
    wasmAvailable = await enableWasm();
  });

  afterAll(() => {
    disableWasm();
  });

  it("reports wasm availability", () => {
    if (!wasmAvailable) {
      console.warn(
        "goldenflow wasm artifact not built (src/core/wasm/artifacts/*.wasm absent) " +
          "-- skipping the wasm autocorrect-kernel leg. Expected outside the wasm_flow CI lane.",
      );
    }
    expect(true).toBe(true);
  });

  it.skipIf(!wasmAvailable)("fuzzRatio + build_canonical_map via category_auto_correct", () => {
    const backend = getFlowWasmBackend() as FlowWasmBackend;
    if (!backend) throw new Error("wasmAvailable=true but getFlowWasmBackend() returned null");

    for (const [a, b, expected] of FUZZ) {
      expect(backend.fuzzRatio(a, b)).toBeCloseTo(expected, 9);
    }

    // With the wasm backend active, the transform dispatches through
    // build_canonical_map -> same corrected column.
    const transform = getTransform("category_auto_correct");
    if (!transform) throw new Error("category_auto_correct not registered");
    const result = transform.func(scenarioColumn()) as ColumnValue[];
    expectScenario(result);
  });
});
