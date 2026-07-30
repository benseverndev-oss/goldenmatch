/**
 * WASM frame-kernel reroute parity (Wave 1b, un-deferred).
 *
 * With the analysis-wasm backend enabled, `nUnique` / `duplicateRowRatio` intern
 * homogeneously-typed number/string columns through the SHARED `analysis-core`
 * canon (the same `intern_f64`/`intern_str` + `canon_f64_bits` the Python native
 * wheel runs), so the equality semantics are ONE source of truth. This locks that
 * the kernel path reproduces the Python-locked `frame_kernels_adversarial.json`
 * fixture EXACTLY (== pure-TS), across the adversarial cases: `-0.0`/`+0.0` fold,
 * `NaN` fold, `NaN` vs null, empty-string vs null, int-vs-float columns, and a
 * multi-column mixed frame.
 *
 * Skipped when the built artifact is absent (default checkout / no toolchain);
 * the CI `analysis_wasm` lane builds it first and runs this un-skipped.
 */
import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it, afterAll } from "vitest";
import { duplicateRowRatio, nUnique, nullRatioPerColumn } from "../../src/core/aggregate.js";
import { enableAnalysisWasm, disableAnalysisWasm } from "../../src/core/wasm/index.js";
import type { FrameRows } from "../../src/core/types.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURE = join(__dirname, "..", "fixtures", "frame_kernels_adversarial.json");
const artifact = fileURLToPath(
  new URL("../../src/core/wasm/artifacts/analysis_wasm_bg.wasm", import.meta.url),
);
const d = existsSync(artifact) ? describe : describe.skip;

// Mirror of the Python `SCENARIOS` (test_frame_kernels_parity.py). Every column
// here is homogeneously typed (number or string), so every one routes through the
// shared kernel — no pure-TS fallback is exercised by this fixture.
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

d("parity: WASM frame kernels vs python-locked fixture (shared intern canon)", () => {
  afterAll(() => disableAnalysisWasm());

  it("enableAnalysisWasm() succeeds in this lane", async () => {
    disableAnalysisWasm();
    expect(await enableAnalysisWasm()).toBe(true);
    disableAnalysisWasm();
  });

  it("distinct / dup_ratio via the shared kernel match the fixture EXACTLY", async () => {
    const expected = JSON.parse(readFileSync(FIXTURE, "utf-8"));
    // Pure-TS baseline (backend off) — the classified fallback.
    disableAnalysisWasm();
    const pureTs = Object.fromEntries(
      Object.entries(SCENARIOS).map(([name, cols]) => [name, kernels(cols)]),
    );
    // Kernel path (backend on).
    expect(await enableAnalysisWasm()).toBe(true);
    const kernel = Object.fromEntries(
      Object.entries(SCENARIOS).map(([name, cols]) => [name, kernels(cols)]),
    );
    disableAnalysisWasm();

    expect(kernel).toEqual(expected); // kernel == Python-locked fixture
    expect(kernel).toEqual(pureTs); // kernel == pure-TS fallback (one canon)
  });
});
