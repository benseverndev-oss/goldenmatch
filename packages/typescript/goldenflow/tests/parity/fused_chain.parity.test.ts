/**
 * Fused columnar apply (Pillar-1 on the edge) — parity guard.
 *
 * When the WASM backend is active, a run of owned no-arg string transforms fuses
 * into ONE `applyChain` crossing. It must be byte-identical to the per-transform
 * path — same output rows AND same audit manifest — so the fusion is transparent
 * except for the number of JS<->WASM boundary crossings.
 *
 * The fused path only engages when the `.wasm` artifact is built (a CI-only build
 * product, never committed — see src/core/wasm/artifacts/.gitignore). Outside the
 * `wasm_flow` lane `enableWasm()` resolves false and these legs skip.
 */
import { describe, it, expect, beforeAll, afterAll } from "vitest";

import { TransformEngine } from "../../src/core/engine/transformer.js";
import { FUSABLE_KERNELS } from "../../src/core/engine/_chain.js";
import { getFlowWasmBackend } from "../../src/core/wasm/backend.js";
import { enableWasm, disableWasm } from "../../src/core/wasm/index.js";
import type { Row } from "../../src/core/types.js";

let wasmAvailable = false;

beforeAll(async () => {
  wasmAvailable = await enableWasm();
  // Start each parity comparison from a known-off state; the tests toggle.
  disableWasm();
});

afterAll(() => {
  disableWasm();
});

const SAMPLE: Row[] = [
  { v: "  <b>John</b>  SMITH!  http://x.com/y " },
  { v: "o'BRIEN, jr.  123" },
  { v: null },
  { v: "  a   b  “Q” " },
  { v: "" },
  { v: "café  éé  #7" },
];

const CHAINS: string[][] = [
  ["strip", "lowercase"],
  ["strip", "lowercase", "collapse_whitespace", "remove_punctuation"],
  ["remove_html_tags", "remove_urls", "strip", "collapse_whitespace"],
  ["normalize_unicode", "lowercase", "remove_digits"],
  ["strip", "lowercase", "email_normalize", "email_canonical"],
  ["name_transliterate", "name_proper", "strip_titles", "strip_suffixes"],
];

function run(ops: string[]): { rows: readonly Row[]; records: unknown[] } {
  const engine = new TransformEngine({ transforms: [{ column: "v", ops }] });
  const result = engine.transformDf(SAMPLE.map((r) => ({ ...r })));
  const records = result.manifest.records.map((r) => ({
    column: r.column,
    transform: r.transform,
    affectedRows: r.affectedRows,
    sampleBefore: r.sampleBefore,
    sampleAfter: r.sampleAfter,
  }));
  return { rows: result.rows, records };
}

describe("goldenflow fused chain: reports wasm availability", () => {
  it("wasm artifact present?", () => {
    if (!wasmAvailable) {
      console.warn(
        "goldenflow wasm artifact not built -- skipping the fused-chain parity legs " +
          "(expected outside the wasm_flow CI lane).",
      );
    }
    expect(true).toBe(true);
  });
});

describe("goldenflow fused chain: coverage guard", () => {
  it.skipIf(!wasmAvailable)(
    "FUSABLE_KERNELS mirrors the backend fusableKernelNames()",
    async () => {
      await enableWasm();
      const backend = getFlowWasmBackend();
      expect(backend).not.toBeNull();
      const native = new Set(backend!.fusableKernelNames());
      expect(native).toEqual(new Set(FUSABLE_KERNELS));
      disableWasm();
    },
  );
});

describe("goldenflow fused chain: fused === per-transform", () => {
  for (const ops of CHAINS) {
    it.skipIf(!wasmAvailable)(
      `[${ops.join(" -> ")}] fused matches pure-TS`,
      async () => {
        // Reference: pure-TS, per-transform.
        disableWasm();
        const ref = run(ops);
        // Fused: WASM backend active -> the run fuses into one applyChain.
        await enableWasm();
        const fused = run(ops);
        disableWasm();

        expect(fused.rows).toEqual(ref.rows);
        expect(fused.records).toEqual(ref.records);
      },
    );
  }
});
