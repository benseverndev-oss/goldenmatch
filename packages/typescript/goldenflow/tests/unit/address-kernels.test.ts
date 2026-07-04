/**
 * Pinned-vector parity for the multi-output `split_address` kernel (Wave D
 * address-simple): one input column -> [street, city, state, zip]. It's a
 * dataframe-mode transform whose scalar core (`splitAddressTs`) doesn't fit the
 * shared string->scalar corpus in `tests/parity/identifiers.parity.test.ts`, so
 * it's asserted here with the SAME pinned vectors as the Python
 * `tests/transforms/test_address_kernels.py`.
 *
 * The 7 scalar address transforms (address_standardize/address_expand/
 * state_abbreviate/state_expand/zip_normalize/country_standardize/
 * unit_normalize) DO fit the shared corpus and are covered there.
 *
 * The pure-TS leg always runs; the WASM leg runs only when the `.wasm` artifact
 * has been built (a CI-only build product, never committed) -- exactly the
 * skip-guarded pattern from the identifier / name-kernel parity harnesses.
 */
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { splitAddressTs } from "../../src/core/transforms/address.js";
import { enableWasm, disableWasm } from "../../src/core/wasm/index.js";
import { getFlowWasmBackend, type FlowWasmBackend } from "../../src/core/wasm/backend.js";

// (input, expected street, city, state, zip) -- mirrors _SPLIT_ADDRESS in
// test_address_kernels.py. `null` mirrors Python `None` (the null row).
const SPLIT_ADDRESS: Array<
  [string | null, string | null, string | null, string | null, string | null]
> = [
  ["123 Main St, Springfield, IL 62704", "123 Main St", "Springfield", "IL", "62704"],
  ["1 Park Ave, New York, NY 10001-2345", "1 Park Ave", "New York", "NY", "10001-2345"],
  ["  9 Elm Rd, Denver, CO 80014  ", "9 Elm Rd", "Denver", "CO", "80014"],
  [
    "123 Main St, Apt 4, Springfield, IL 62704",
    "123 Main St",
    "Apt 4, Springfield",
    "IL",
    "62704",
  ],
  ["  just a street  ", "  just a street  ", null, null, null],
  [
    "123 Main St, Springfield, ILL 62704",
    "123 Main St, Springfield, ILL 62704",
    null,
    null,
    null,
  ],
  [null, null, null, null, null],
];

/** Run the scalar split fn, short-circuiting the null row to all-null the way
 * the dataframe transform does (the scalar fn doesn't accept null). */
function runSplit(
  fn: (s: string) => [string, string | null, string | null, string | null],
  input: string | null,
): [string | null, string | null, string | null, string | null] {
  return input === null ? [null, null, null, null] : fn(input);
}

/** Same via a wasm backend method returning a 4-element (string | null)[]. */
function runSplitWasm(
  fn: (s: string) => (string | null)[],
  input: string | null,
): [string | null, string | null, string | null, string | null] {
  if (input === null) return [null, null, null, null];
  const [street, city, state, zip] = fn(input);
  return [street ?? null, city ?? null, state ?? null, zip ?? null];
}

describe("goldenflow address kernels: pure-TS matches pinned vectors", () => {
  it("split_address", () => {
    for (const [input, street, city, state, zip] of SPLIT_ADDRESS) {
      expect(runSplit(splitAddressTs, input)).toEqual([street, city, state, zip]);
    }
  });
});

describe("goldenflow address kernels: wasm matches pinned vectors", () => {
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
          "-- skipping the wasm address-kernel leg. Expected outside the wasm_flow CI lane.",
      );
    }
    expect(true).toBe(true);
  });

  it.skipIf(!wasmAvailable)("split_address", () => {
    const backend = getFlowWasmBackend() as FlowWasmBackend;
    if (!backend) throw new Error("wasmAvailable=true but getFlowWasmBackend() returned null");

    for (const [input, street, city, state, zip] of SPLIT_ADDRESS) {
      expect(runSplitWasm((s) => backend.splitAddress(s), input)).toEqual([
        street,
        city,
        state,
        zip,
      ]);
    }
  });
});
