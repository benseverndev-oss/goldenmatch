/**
 * SPIKE — pure-TS == WASM-kernel equivalence for the levenshtein tracer.
 *
 * The TS half of the single-kernel-collapse feasibility spike (see
 * `context-network/decisions/0016-single-kernel-collapse-spike.md` and
 * `scripts/check_kernel_equivalence.py` for the Python half). It answers
 * kill-criterion item (1) on the TS binding and is INPUT to item (2) (WASM
 * loads in this JS target): does the pure-TS `levenshtein` reproduce the
 * `score-wasm` kernel (the same `score-core` crate the Python wheel wraps) to
 * 4 decimals?
 *
 * STANDALONE / additive: it only calls `scoreMatrix` (the existing public entry)
 * with the WASM backend on vs off and compares. It changes no default — pure-TS
 * stays the default; `disableWasm()` resets in `afterAll`.
 *
 * Skipped when the built `.wasm` artifact is absent (default checkout / no
 * rustup wasm toolchain), mirroring `tests/parity/wasm-scorer.test.ts`. The CI
 * `wasm_score` lane builds the artifact (`score-wasm/build_wasm.sh`) and runs
 * this un-skipped. Until then this is scaffolding-pending-CI, reported honestly
 * in the spike writeup.
 *
 * NOTE the doc gotcha from the TS package CLAUDE.md: as of #879 the pure-TS
 * scorers were ALIGNED with rapidfuzz (the WASM kernel), so pure-TS ≈ WASM now
 * holds for levenshtein. If a future divergence reappears this gate catches it.
 */
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { enableWasm, disableWasm } from "../../src/core/wasm/index.js";
import { scoreMatrix, levenshteinSimilarity } from "../../src/core/scorer.js";

const artifact = fileURLToPath(
  new URL("../../src/core/wasm/artifacts/score_wasm_bg.wasm", import.meta.url),
);
const hasArtifact = existsSync(artifact);
const d = hasArtifact ? describe : describe.skip;

const TOL = 1e-4; // project-wide 4-decimal scorer parity contract

// Adversarial + name-shaped corpus: identical, transposition, case, accented
// BMP, CJK, long strings, empty. Mirrors the Python gate's edge battery.
const CORPUS: readonly string[] = [
  "",
  "a",
  "abc",
  "acb",
  "ABC",
  "smith",
  "smyth",
  "johnson",
  "jonson",
  "café",
  "cafe",
  "naïve",
  "naive",
  "über",
  "uber",
  "日本語",
  "日本",
  "x".repeat(200),
  "y" + "x".repeat(199),
  "john smith",
  "jon smith",
];

d("spike: pure-TS levenshtein == score-wasm kernel (4dp)", () => {
  beforeAll(async () => {
    const ok = await enableWasm({ require: true });
    expect(ok).toBe(true);
  });
  afterAll(() => {
    disableWasm();
  });

  it("scoreMatrix(levenshtein) is 4dp-equal pure-TS vs WASM over the corpus", () => {
    // WASM ON: scoreMatrix routes through the kernel for the covered scorer.
    const wasmMatrix = scoreMatrix([...CORPUS], "levenshtein");

    // Pure-TS reference: compute the same off-diagonal cells directly (this is
    // exactly what scoreMatrix does with the backend OFF, but we compute it
    // explicitly so the comparison can't accidentally read the same backend).
    let maxDiff = 0;
    let worst = "";
    for (let i = 0; i < CORPUS.length; i++) {
      for (let j = i + 1; j < CORPUS.length; j++) {
        const pure = levenshteinSimilarity(CORPUS[i]!, CORPUS[j]!);
        const kern = wasmMatrix[i]![j]!;
        const diff = Math.abs(pure - kern);
        if (diff > maxDiff) {
          maxDiff = diff;
          worst = `${CORPUS[i]} vs ${CORPUS[j]} pure=${pure} kernel=${kern}`;
        }
        expect(
          diff,
          `levenshtein divergence > 4dp: ${CORPUS[i]} vs ${CORPUS[j]} pure=${pure} kernel=${kern}`,
        ).toBeLessThanOrEqual(TOL);
      }
    }
    // Surface the max diff for the bench/writeup even on success.
    // eslint-disable-next-line no-console
    console.log(`[spike] pure-TS vs WASM levenshtein max abs diff = ${maxDiff} (worst: ${worst})`);
    expect(maxDiff).toBeLessThanOrEqual(TOL);
  });
});
