import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  resolveJsonPure,
  applyDecisionJsonPure,
  evaluateBuiltinJsonPure,
  autoConfigJsonPure,
  skipIfFalsyJsonPure,
} from "../../src/core/wasm/plannerJsonPure.js";

const VEC = (name: string) =>
  fileURLToPath(
    new URL(
      `../../../../rust/extensions/goldenpipe-core/tests/vectors/${name}.json`,
      import.meta.url,
    ),
  );
const load = (name: string) =>
  JSON.parse(readFileSync(VEC(name), "utf8")) as Array<{ input: unknown; expected: unknown }>;

const FAMILIES: Array<[string, (s: string) => string]> = [
  ["resolve", resolveJsonPure],
  ["apply_decision", applyDecisionJsonPure],
  ["evaluate_builtin", evaluateBuiltinJsonPure],
  ["auto_config", autoConfigJsonPure],
  ["skip_if", skipIfFalsyJsonPure],
];

describe("Leg A — pure-TS planner == goldenpipe-core golden vectors", () => {
  for (const [name, fn] of FAMILIES) {
    it(`${name} vectors`, () => {
      for (const { input, expected } of load(name)) {
        expect(JSON.parse(fn(JSON.stringify(input)))).toEqual(expected);
      }
    });
  }
});
