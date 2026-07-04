/**
 * Pinned-vector parity for the multi-output names-remainder kernels
 * (`split_name` / `split_name_reverse` -> [first, last], and `merge_name`
 * first+last -> full). These are dataframe-mode transforms whose scalar cores
 * (`splitNameTs` / `splitNameReverseTs` / `mergeNameTs`) don't fit the shared
 * string->scalar corpus in `tests/parity/identifiers.parity.test.ts`, so they
 * are asserted here with the SAME pinned vectors as the Python
 * `tests/transforms/test_name_kernels.py`.
 *
 * The pure-TS leg always runs; the WASM leg runs only when the `.wasm`
 * artifact has been built (a CI-only build product, never committed) --
 * exactly the skip-guarded pattern from the identifier parity harness.
 */
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import {
  splitNameTs,
  splitNameReverseTs,
  mergeNameTs,
} from "../../src/core/transforms/names.js";
import { enableWasm, disableWasm } from "../../src/core/wasm/index.js";
import { getFlowWasmBackend, type FlowWasmBackend } from "../../src/core/wasm/backend.js";

// (input, expected first, expected last) -- mirrors _SPLIT_NAME in
// test_name_kernels.py. `null` mirrors Python `None` (the null row).
const SPLIT_NAME: Array<[string | null, string | null, string | null]> = [
  ["John Smith", "John", "Smith"],
  ["John Michael Smith", "John Michael", "Smith"],
  ["Madonna", "Madonna", ""],
  ["  Jane  Doe  ", "Jane ", "Doe"],
  [null, null, null],
];

// (input, expected first, expected last) -- mirrors _SPLIT_NAME_REVERSE.
const SPLIT_NAME_REVERSE: Array<[string | null, string | null, string | null]> = [
  ["Smith, John", "John", "Smith"],
  ["Smith,John", "John", "Smith"],
  ["Smith, John, Jr", "John, Jr", "Smith"],
  ["Madonna", "Madonna", ""],
  [null, null, null],
];

// (first, last, expected full) -- mirrors _MERGE_NAME. `null` -> Python `None`
// (both a missing part and the "both absent/blank -> None" result).
const MERGE_NAME: Array<[string | null, string | null, string | null]> = [
  ["John", "Smith", "John Smith"],
  ["John", null, "John"],
  [null, null, null],
  ["  John  ", "Smith", "  John   Smith"],
  [null, null, null],
];

/** Run a scalar split fn, short-circuiting the null row to [null, null] the
 * way the dataframe transform does (the scalar fns don't accept null). */
function runSplit(
  fn: (s: string) => [string, string],
  input: string | null,
): [string | null, string | null] {
  return input === null ? [null, null] : fn(input);
}

/** Same via a wasm backend method returning a 2-element string[]. */
function runSplitWasm(
  fn: (s: string) => string[],
  input: string | null,
): [string | null, string | null] {
  if (input === null) return [null, null];
  const [first, last] = fn(input);
  return [first ?? null, last ?? null];
}

describe("goldenflow name kernels: pure-TS matches pinned vectors", () => {
  it("split_name", () => {
    for (const [input, first, last] of SPLIT_NAME) {
      expect(runSplit(splitNameTs, input)).toEqual([first, last]);
    }
  });

  it("split_name_reverse", () => {
    for (const [input, first, last] of SPLIT_NAME_REVERSE) {
      expect(runSplit(splitNameReverseTs, input)).toEqual([first, last]);
    }
  });

  it("merge_name", () => {
    for (const [first, last, full] of MERGE_NAME) {
      expect(mergeNameTs(first, last) ?? null).toEqual(full);
    }
  });
});

describe("goldenflow name kernels: wasm matches pinned vectors", () => {
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
          "-- skipping the wasm name-kernel leg. Expected outside the wasm_flow CI lane.",
      );
    }
    expect(true).toBe(true);
  });

  it.skipIf(!wasmAvailable)("split_name / split_name_reverse / merge_name", () => {
    const backend = getFlowWasmBackend() as FlowWasmBackend;
    if (!backend) throw new Error("wasmAvailable=true but getFlowWasmBackend() returned null");

    for (const [input, first, last] of SPLIT_NAME) {
      expect(runSplitWasm((s) => backend.splitName(s), input)).toEqual([first, last]);
    }
    for (const [input, first, last] of SPLIT_NAME_REVERSE) {
      expect(runSplitWasm((s) => backend.splitNameReverse(s), input)).toEqual([first, last]);
    }
    for (const [first, last, full] of MERGE_NAME) {
      expect(backend.mergeName(first, last) ?? null).toEqual(full);
    }
  });
});
