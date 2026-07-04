/**
 * Pinned-vector parity for the PARAMETERIZED text kernels (Wave D text-1):
 * `truncate(n)` / `pad_left(width, char)` / `pad_right(width, char)`. These
 * carry per-column-constant params, so a single `(transform, input) ->
 * expected` corpus row can't express the non-default-param cases -- they're
 * asserted here with the SAME pinned vectors as the Python
 * `tests/transforms/test_text_kernels.py`, against the goldenflow-core Rust
 * kernel's values.
 *
 * The 10 non-parameterized text transforms (strip/collapse_whitespace/... /
 * extract_numbers) DO fit the shared string->scalar corpus and are covered in
 * `tests/parity/identifiers.parity.test.ts`.
 *
 * The pure-TS leg always runs; the WASM leg runs only when the `.wasm` artifact
 * has been built (a CI-only build product, never committed) -- exactly the
 * skip-guarded pattern from the identifier / name / address kernel harnesses.
 */
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { truncateTs, padLeftTs, padRightTs } from "../../src/core/transforms/text.js";
import { enableWasm, disableWasm } from "../../src/core/wasm/index.js";
import { getFlowWasmBackend, type FlowWasmBackend } from "../../src/core/wasm/backend.js";

// (input, expected) rows. `null` mirrors the dataframe transform short-circuit:
// a null cell stays null (never reaches the kernel), so it's asserted outside
// the scalar fn just like the Python column-level `None` rows.
const TRUNCATE: Array<[number, string | null, string | null]> = [
  [5, "hello world", "hello"],
  [5, "hi", "hi"],
  [0, "abc", ""],
  [3, "", ""],
  [4, "cafés", "café"], // char-based, not byte
  [5, null, null],
];

const PAD_LEFT: Array<[number, string, string | null, string | null]> = [
  [5, "0", "42", "00042"],
  [3, "0", "already", "already"], // len >= width -> unchanged
  [5, "0", null, null],
];

const PAD_RIGHT: Array<[number, string, string | null, string | null]> = [
  [5, " ", "42", "42   "],
  [4, ".", "ab", "ab.."],
  [3, " ", "already", "already"], // len >= width -> unchanged
  [5, " ", null, null],
];

describe("goldenflow text kernels: pure-TS matches pinned vectors", () => {
  it("truncate", () => {
    for (const [n, input, expected] of TRUNCATE) {
      expect(input === null ? null : truncateTs(input, n)).toEqual(expected);
    }
  });

  it("pad_left", () => {
    for (const [width, char, input, expected] of PAD_LEFT) {
      expect(input === null ? null : padLeftTs(input, width, char)).toEqual(expected);
    }
  });

  it("pad_right", () => {
    for (const [width, char, input, expected] of PAD_RIGHT) {
      expect(input === null ? null : padRightTs(input, width, char)).toEqual(expected);
    }
  });
});

describe("goldenflow text kernels: wasm matches pinned vectors", () => {
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
          "-- skipping the wasm text-kernel leg. Expected outside the wasm_flow CI lane.",
      );
    }
    expect(true).toBe(true);
  });

  it.skipIf(!wasmAvailable)("truncate / pad_left / pad_right", () => {
    const backend = getFlowWasmBackend() as FlowWasmBackend;
    if (!backend) throw new Error("wasmAvailable=true but getFlowWasmBackend() returned null");

    for (const [n, input, expected] of TRUNCATE) {
      expect(input === null ? null : backend.truncate(input, n)).toEqual(expected);
    }
    for (const [width, char, input, expected] of PAD_LEFT) {
      expect(input === null ? null : backend.padLeft(input, width, char)).toEqual(expected);
    }
    for (const [width, char, input, expected] of PAD_RIGHT) {
      expect(input === null ? null : backend.padRight(input, width, char)).toEqual(expected);
    }
  });
});
