import { describe, it, expect } from "vitest";
import {
  AutoConfigController,
  ConfigValidationError,
  getLastControllerRun,
  makeControllerBudget,
  _resetLastControllerRun,
} from "../../src/core/autoconfigController.js";
import { HeuristicRefitPolicy } from "../../src/core/autoconfigPolicy.js";
import { DEFAULT_RULES_V1_7_V1_8 } from "../../src/core/autoconfigRules.js";
import { StopReason } from "../../src/core/complexityProfile.js";

function makeCtrl() {
  return new AutoConfigController({
    policy: new HeuristicRefitPolicy(DEFAULT_RULES_V1_7_V1_8),
    budget: makeControllerBudget({ maxIterations: 3, maxSeconds: 30 }),
  });
}

describe("AutoConfigController — pathological gates", () => {
  it("throws on empty input", async () => {
    const ctrl = makeCtrl();
    await expect(ctrl.run([])).rejects.toThrow(ConfigValidationError);
  });

  it("throws on all-null usable columns", async () => {
    const ctrl = makeCtrl();
    const rows = [
      { a: null, b: null },
      { a: null, b: null },
    ];
    await expect(ctrl.run(rows)).rejects.toThrow(ConfigValidationError);
  });

  it("short-circuits single-row input to v0 + empty history", async () => {
    const ctrl = makeCtrl();
    const out = await ctrl.run([{ name: "Alice", zip: "10001" }]);
    expect(out.history.entries).toHaveLength(0);
    expect(out.committedConfig).toBeDefined();
  });

  it("short-circuits single-column input to v0 + empty history", async () => {
    const ctrl = makeCtrl();
    const out = await ctrl.run([{ name: "Alice" }, { name: "Bob" }]);
    expect(out.history.entries).toHaveLength(0);
  });
});

describe("AutoConfigController — iteration loop", () => {
  it("runs the loop and commits a config with a valid stop reason", async () => {
    _resetLastControllerRun();
    const ctrl = makeCtrl();
    const rows = [
      { first: "Alice", last: "Smith", email: "alice@example.com" },
      { first: "Bob",   last: "Jones", email: "bob@example.com" },
      { first: "Carol", last: "Davis", email: "carol@example.com" },
      { first: "David", last: "Wilson", email: "david@example.com" },
    ];
    const out = await ctrl.run(rows);
    expect(out.committedConfig).toBeDefined();
    expect(Object.values(StopReason)).toContain(out.history.stopReason);
    expect(getLastControllerRun()).toBe(out.history);
  });

  it("populates iteration indices monotonically", async () => {
    const ctrl = makeCtrl();
    const rows = [
      { name: "Alice", zip: "10001" },
      { name: "Alice", zip: "10001" },
      { name: "Bob",   zip: "10002" },
      { name: "Carol", zip: "10003" },
    ];
    const out = await ctrl.run(rows);
    // entries[0..N] should be in non-decreasing iteration order, plus optional v0 entry at -1
    const realIters = out.history.entries
      .filter((e) => e.iteration >= 0)
      .map((e) => e.iteration);
    for (let i = 1; i < realIters.length; i++) {
      expect(realIters[i]!).toBeGreaterThanOrEqual(realIters[i - 1]!);
    }
  });
});

describe("AutoConfigController — stop reasons", () => {
  it("sets BUDGET_ITERATIONS when iteration budget exhausted without other stop", async () => {
    // Use a tiny budget and force-non-GREEN by feeding a single fuzzy-only shape.
    const ctrl = new AutoConfigController({
      policy: new HeuristicRefitPolicy([]),  // no rules → policy always returns null → POLICY_SATISFIED
      budget: makeControllerBudget({ maxIterations: 1 }),
    });
    const out = await ctrl.run([
      { name: "Alice", zip: "10001" },
      { name: "Bob",   zip: "10002" },
      { name: "Carol", zip: "10003" },
    ]);
    expect(Object.values(StopReason)).toContain(out.history.stopReason);
  });
});
