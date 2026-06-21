/**
 * autoconfig-wasm-planner-equivalence.test.ts
 *
 * Proves the OPT-IN wasm planner path is byte-identical to the default pure-TS
 * path: for every Python-generated planner fixture, `applyPlannerRules` returns
 * the same ExecutionPlan whether the wasm backend is enabled or not. Combined
 * with `planner.parity.test.ts` (TS rules == Python) and
 * `autoconfig-core.parity.test.ts` (wasm == Python golden vectors), this closes
 * the loop: pure-TS rules ≡ wasm core ≡ Python, so enabling the wasm core is a
 * safe, behavior-preserving swap (it just makes the agreement load-bearing).
 */
import { describe, it, expect, afterEach } from "vitest";
import fixturesRaw from "./v2-fixtures.json" with { type: "json" };
import { applyPlannerRules } from "../../src/core/autoconfigPlanner.js";
import { DEFAULT_PLANNER_RULES } from "../../src/core/autoconfigPlannerRules.js";
import {
  makeComplexityProfile,
  makeBlockingProfile,
} from "../../src/core/complexityProfile.js";
import { makeRuntimeProfile } from "../../src/core/runtimeProfile.js";
import {
  enableAutoconfigWasm,
  disableAutoconfigWasm,
} from "../../src/core/autoconfigWasm.js";
import { isAutoconfigWasmEnabled } from "../../src/core/autoconfigWasmBackend.js";

interface PlannerCase {
  name: string;
  input: {
    n_rows: number;
    pair_count: number;
    ram_gb: number;
    cpu_count: number;
    disk_gb: number;
    user_backend: string | null;
  };
}

const cases = (fixturesRaw as { planner: PlannerCase[] }).planner;

afterEach(() => {
  // Always restore the pure-TS default so no other test sees the wasm backend.
  disableAutoconfigWasm();
});

describe("autoconfig planner: wasm path ≡ pure-TS path", () => {
  it("defaults to pure-TS (wasm not enabled)", () => {
    expect(isAutoconfigWasmEnabled()).toBe(false);
  });

  for (const c of cases) {
    it(`equivalent: ${c.name}`, () => {
      const profile = makeComplexityProfile({
        blocking: makeBlockingProfile({ totalComparisons: c.input.pair_count }),
      });
      const runtime = makeRuntimeProfile({
        availableRamGb: c.input.ram_gb,
        cpuCount: c.input.cpu_count,
        diskFreeGb: c.input.disk_gb,
      });
      const ctx = { userBackend: c.input.user_backend };

      disableAutoconfigWasm();
      const tsPlan = applyPlannerRules(
        profile,
        runtime,
        c.input.n_rows,
        DEFAULT_PLANNER_RULES,
        ctx,
      );

      enableAutoconfigWasm();
      expect(isAutoconfigWasmEnabled()).toBe(true);
      const wasmPlan = applyPlannerRules(
        profile,
        runtime,
        c.input.n_rows,
        DEFAULT_PLANNER_RULES,
        ctx,
      );

      expect(wasmPlan).toEqual(tsPlan);
    });
  }
});
