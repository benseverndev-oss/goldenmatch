/**
 * Leg B — goldenpipe-wasm == goldenpipe-core golden vectors.
 *
 * The cross-surface native leg: the SAME golden vectors Leg A replays through
 * pure-TS are here driven through the REAL wasm kernel (goldenpipe-core compiled
 * to wasm via goldenpipe-wasm). Skipped when the artifact is absent (a default
 * checkout has no `src/core/wasm/artifacts/`); the CI `goldenpipe_wasm` lane
 * builds it, so this un-skips and becomes the gate there. Mirrors goldenflow's
 * identifier wasm-parity leg.
 */
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { enableWasm, disableWasm } from "../../src/core/wasm/index.js";
import { getPipeWasmBackend, type PipeWasmBackend } from "../../src/core/wasm/backend.js";

const ARTIFACT = fileURLToPath(
  new URL("../../src/core/wasm/artifacts/goldenpipe_wasm_bg.wasm", import.meta.url),
);
const VEC = (name: string) =>
  fileURLToPath(
    new URL(
      `../../../../rust/extensions/goldenpipe-core/tests/vectors/${name}.json`,
      import.meta.url,
    ),
  );
const load = (name: string) =>
  JSON.parse(readFileSync(VEC(name), "utf8")) as Array<{ input: unknown; expected: unknown }>;

// Skip locally (no artifact); the CI lane builds the artifact and un-skips.
const suite = existsSync(ARTIFACT) ? describe : describe.skip;

suite("Leg B — goldenpipe-wasm == golden vectors", () => {
  beforeAll(async () => {
    await enableWasm({ require: true }); // URL/fs default resolves the built artifact
  });
  afterAll(() => disableWasm());

  const call = (family: string, input: string): string => {
    const b = getPipeWasmBackend() as PipeWasmBackend;
    const dispatch: Record<string, (s: string) => string> = {
      resolve: b.resolveJson,
      apply_decision: b.applyDecisionJson,
      evaluate_builtin: b.evaluateBuiltinJson,
      auto_config: b.autoConfigJson,
      skip_if: b.skipIfFalsyJson,
    };
    return dispatch[family]!(input);
  };

  for (const family of ["resolve", "apply_decision", "evaluate_builtin", "auto_config", "skip_if"]) {
    it(`${family} vectors`, () => {
      for (const { input, expected } of load(family)) {
        expect(JSON.parse(call(family, JSON.stringify(input)))).toEqual(expected);
      }
    });
  }
});
