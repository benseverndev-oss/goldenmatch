/**
 * Cross-surface parity: the TS wasm healer binding vs the SAME golden vectors
 * the Rust oracle authored (`suggest-core/tests/golden/suggest/*.json`, copied
 * into the TS fixtures by `scripts/build_suggest_wasm.mjs`) and the Python
 * native path asserts (`test_suggest_wasm_crossparity.py`). Rust + Python + TS
 * green on identical JSON == one kernel, three surfaces, zero drift.
 *
 * Drives the real wasm kernel through `suggestReview(input)`; the fixture
 * `input` IS the packed five-JSON-string {@link SuggestKernelInput}. Skips
 * gracefully if the wasm can't init in this env (autoconfig parity proves it
 * normally does under vitest).
 */
import { describe, it, expect, afterAll } from "vitest";
import {
  enableSuggestWasm,
  suggestReview,
  disableSuggestWasm,
} from "../../src/core/suggestWasm.js";
import type { SuggestKernelInput } from "../../src/core/suggestWasmBackend.js";
import emptyCase from "./fixtures/suggest/empty.json" with { type: "json" };
import lowerThreshold from "./fixtures/suggest/lower_threshold.json" with { type: "json" };
import raiseThreshold from "./fixtures/suggest/raise_threshold.json" with { type: "json" };
import swapScorer from "./fixtures/suggest/swap_scorer.json" with { type: "json" };
import addNegativeEvidence from "./fixtures/suggest/add_negative_evidence.json" with { type: "json" };

interface Fixture {
  readonly input: SuggestKernelInput;
  readonly expected: readonly unknown[];
}

const CASES: readonly [string, Fixture][] = [
  ["empty", emptyCase as unknown as Fixture],
  ["lower_threshold", lowerThreshold as unknown as Fixture],
  ["raise_threshold", raiseThreshold as unknown as Fixture],
  ["swap_scorer", swapScorer as unknown as Fixture],
  ["add_negative_evidence", addNegativeEvidence as unknown as Fixture],
];

// Init once at collection time so we can statically skip if wasm is unavailable.
const enabled = enableSuggestWasm();
const maybe = enabled ? it : it.skip;

afterAll(() => disableSuggestWasm());

describe("suggest-wasm kernel parity (TS == golden fixtures)", () => {
  for (const [name, fx] of CASES) {
    maybe(`${name}: wasm output deep-equals the golden expected`, () => {
      const raw = suggestReview(fx.input);
      const parsed = JSON.parse(raw);
      expect(parsed).toEqual(fx.expected);
    });
  }
});
