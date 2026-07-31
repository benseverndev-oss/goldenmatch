/**
 * fs-default-routing-reached.test.ts -- the T2 "workload reached" conformance
 * check (conformance v2, 0047 amendment; the follow-on to the T1 default-routing
 * auditor in scripts/check_thesis_conformance.py).
 *
 * T1 (the Python static gate) verifies the batteries entry (`src/index.ts`)
 * AUTO-REGISTERS the fs-core kernel at import -- i.e. the default GATE exists.
 * Its documented honest limit: it does NOT prove the default caller path actually
 * ROUTES THROUGH the registered backend on a real workload. A deliberately (or
 * accidentally) mis-wired pipeline -- one that registers the backend but never
 * consults it -- would pass T1 and the flag test while silently running the
 * pure-TS fallback, exactly the "owner shipped, default elsewhere" class T1 exists
 * to kill, one layer down.
 *
 * This closes that limit BEHAVIORALLY: importing ONLY the batteries root (no
 * manual `enableFsWasmScoring()` call), a default `runDedupePipeline` on a
 * probabilistic-eligible workload must INVOKE the auto-registered FS backend --
 * the pipeline reaches the owner, not just the flag. A negative control (backend
 * cleared -> the same workload runs pure-TS and never touches the spy) proves the
 * assertion is meaningful, not always-true.
 *
 * Runs in the existing `typescript` CI lane (a vitest unit test) -- no new lane,
 * mirroring T1's "wire into the existing thesis step" discipline. Vitest isolates
 * the module registry per file, so the batteries side-effect here does not leak
 * into the core-only test files that expect the pure-TS default.
 */
import { describe, it, expect, afterEach } from "vitest";
// Import the BATTERIES ROOT for its import-time side-effect ONLY: this is the
// single thing under test -- `src/index.ts` calling registerFsKernel() at import.
// We never call enableFsWasmScoring() ourselves.
import { runDedupePipeline, makeConfig, makeBlockingConfig } from "../../src/index.js";
import type { MatchkeyConfig, Row } from "../../src/core/index.js";
import {
  getFsScoreBackend,
  setFsScoreBackend,
  disableFsWasmScoring,
  isFsWasmScoringEnabled,
} from "../../src/core/fsScoreBackend.js";
import type { FsScoreBackend } from "../../src/core/fsScoreBackend.js";
import { enableFsWasmScoring } from "../../src/core/fsScore.js";

// A probabilistic, kernel-eligible workload: one jaro_winkler field (score_one id
// 0, so `fsRerouteEligible` accepts it) blocked on a shared key so a >=2-row block
// forms and `scoreProbabilisticBlocks` actually has a block to score.
const rows: Row[] = [
  { name: "John", zip: "x" },
  { name: "Jon", zip: "x" },
  { name: "John", zip: "x" },
  { name: "Mary", zip: "y" },
  { name: "Mary", zip: "y" },
];
const probabilisticMk: MatchkeyConfig = {
  name: "fs",
  type: "probabilistic",
  fields: [
    { field: "name", transforms: [], scorer: "jaro_winkler", weight: 1, levels: 3, partialThreshold: 0.8 },
  ],
  linkThreshold: 0.5,
};
const config = makeConfig({
  matchkeys: [probabilisticMk],
  blocking: makeBlockingConfig({ strategy: "static", keys: [{ fields: ["zip"], transforms: [] }] }),
});

/** Wrap the currently-registered backend in a call-counter, delegating both methods. */
function spyOnRegisteredBackend(): { calls: () => number } {
  const real = getFsScoreBackend();
  if (real === null) throw new Error("expected a backend to be auto-registered by the batteries import");
  let n = 0;
  const spy: FsScoreBackend = {
    eligible: (mk) => real.eligible(mk),
    scoreBlock: (blockRows, mk, em, threshold) => {
      n += 1;
      return real.scoreBlock(blockRows, mk, em, threshold);
    },
  };
  setFsScoreBackend(spy);
  return { calls: () => n };
}

afterEach(() => {
  // Restore the batteries default (the real kernel backend) so ordering between
  // this file's own tests can't leave the registry cleared. The per-file module
  // isolation keeps this out of other test files regardless.
  enableFsWasmScoring();
});

describe("T2 default-routing REACHED (batteries workload actually invokes the owner)", () => {
  it("registers the fs backend purely from the batteries import (no manual enable)", () => {
    // Precondition for the whole check: T1's gate is about THIS being true; T2
    // builds on it to prove the pipeline then consults what T1 registered.
    expect(isFsWasmScoringEnabled()).toBe(true);
    expect(getFsScoreBackend()).not.toBeNull();
  });

  it("a default dedupe workload routes probabilistic blocks THROUGH the registered kernel", async () => {
    const spy = spyOnRegisteredBackend();
    const result = await runDedupePipeline(rows, config);

    // The owner was REACHED: the pipeline invoked the auto-registered backend at
    // least once (the "x" block has 3 rows) -- the fallback path never runs while a
    // backend is registered and the matchkey is eligible.
    expect(spy.calls()).toBeGreaterThanOrEqual(1);
    // And the workload really scored (guards against a vacuous pass where the block
    // loop is skipped entirely).
    expect(result.scoredPairs.length).toBeGreaterThanOrEqual(1);
  });

  it("NEGATIVE CONTROL: with the backend cleared, the same workload never touches the spy", async () => {
    // Register a spy, then clear the registry: the pipeline must fall back to
    // pure-TS `scoreProbabilistic` and NOT call the (now-unregistered) spy -- so the
    // positive test's counter tracks real routing, not an always-true assertion.
    const spy = spyOnRegisteredBackend();
    disableFsWasmScoring();
    expect(getFsScoreBackend()).toBeNull();

    const result = await runDedupePipeline(rows, config);
    expect(spy.calls()).toBe(0);
    // The fallback still produces a working result (pure-TS FS scoring).
    expect(result.scoredPairs.length).toBeGreaterThanOrEqual(1);
  });
});
