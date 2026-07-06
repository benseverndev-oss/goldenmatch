/**
 * Cross-surface lock for the frame kernels' EQUALITY SEMANTICS on adversarial values.
 *
 * The frame kernels (`nUnique`, `nullRatioPerColumn`, `duplicateRowRatio`) decide *when
 * two values are the same*. Python/Rust intern via Arrow with an explicit canon
 * (`-0.0`/`+0.0` fold, all `NaN` -> one id, null is its own id); this TS side keys off
 * `JSON.stringify` + a `" null"` sentinel. This test pins that the two surfaces agree
 * on the tricky cases (`-0.0` vs `0.0`, `NaN` vs `NaN` vs null, empty-string vs null,
 * int vs float) against the same Python-locked fixture.
 *
 * `frame_kernels_adversarial.json` is a byte-identical copy of
 * `packages/python/goldenanalysis/tests/fixtures/frame_kernels_adversarial.json` (the
 * file `test_frame_kernels_parity.py` locks). JSON cannot represent `NaN`/`-0.0`
 * inputs, so the frames are built in CODE on both sides (mirrored — keep `SCENARIOS`
 * in lockstep with the Python test) and the fixture holds only the finite outputs.
 *
 * Regression guard: `duplicateRowRatio` once conflated `NaN` and null (both serialized
 * to JSON `null`), over-counting duplicate rows on the `float_nan_null` scenario
 * (reported 1.0 vs Python's 6/7). The `rowKey` fix (per-cell keying) is what this locks.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { duplicateRowRatio, nUnique, nullRatioPerColumn } from "../../src/core/aggregate.js";
import type { FrameRows } from "../../src/core/types.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURE = join(__dirname, "..", "fixtures", "frame_kernels_adversarial.json");

// Mirror of the Python `SCENARIOS` in test_frame_kernels_parity.py. Keep in lockstep.
const SCENARIOS: Record<string, Record<string, unknown[]>> = {
  float_nan_null: { f: [-0.0, 0.0, NaN, NaN, null, 1.0, 1.0] },
  typed_numeric: { i: [5, 5, 3, null, 5], g: [5.0, 5.0, 3.0, null, 5.0] },
  string_empty_null: { s: ["a", "a", "", null, "a", "b", null] },
  mixed: {
    f: [-0.0, 0.0, NaN, NaN, null, 1.0, 1.0],
    i: [5, 5, 3, 3, null, 5, 5],
    s: ["a", "a", "", null, "a", "b", null],
  },
};

function colsToRows(cols: Record<string, unknown[]>): FrameRows {
  const keys = Object.keys(cols);
  const n = cols[keys[0]!]!.length;
  return Array.from({ length: n }, (_, i) => Object.fromEntries(keys.map((k) => [k, cols[k]![i]])));
}

function kernels(cols: Record<string, unknown[]>) {
  const rows = colsToRows(cols);
  const keys = Object.keys(cols);
  return {
    distinct: Object.fromEntries(keys.map((k) => [k, nUnique(rows, k)])),
    null_ratio: nullRatioPerColumn(rows, keys),
    dup_ratio: duplicateRowRatio(rows, keys),
  };
}

describe("parity: frame kernels vs python (adversarial equality semantics)", () => {
  it("distinct / null_ratio / dup_ratio match the python-locked fixture exactly", () => {
    const expected = JSON.parse(readFileSync(FIXTURE, "utf-8"));
    const got = Object.fromEntries(
      Object.entries(SCENARIOS).map(([name, cols]) => [name, kernels(cols)]),
    );
    expect(got).toEqual(expected);
  });
});
