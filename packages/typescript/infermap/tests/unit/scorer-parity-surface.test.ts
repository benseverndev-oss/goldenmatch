// Drift guard for the cross-language scorer parity surface (parity/infermap.yaml).
// SCORER_NAMES / SCORER_KERNELS are declared string sets consumed by the
// api_parity emitter (scripts/emit_ts_surface.mjs). These assertions keep the
// declared sets honest against the actual scorer classes + the WASM backend, so a
// renamed/added scorer or a kernel change can't silently drift the surface.
import { describe, it, expect } from "vitest";
// Narrow imports (not the ./core barrel, which pulls detect.ts -> goldencheck-types
// and needs the workspace dep built) -- mirrors the other unit scorer tests.
import { SCORER_NAMES, SCORER_KERNELS } from "../../src/core/scorers/registry.js";
import { ExactScorer } from "../../src/core/scorers/exact.js";
import { AliasScorer } from "../../src/core/scorers/alias.js";
import { PatternTypeScorer } from "../../src/core/scorers/pattern-type.js";
import { ProfileScorer } from "../../src/core/scorers/profile.js";
import { FuzzyNameScorer } from "../../src/core/scorers/fuzzy-name.js";
import { InitialismScorer } from "../../src/core/scorers/initialism.js";
import { LLMScorer } from "../../src/core/scorers/llm.js";

describe("scorer parity surface", () => {
  it("SCORER_NAMES matches every built-in scorer class .name", () => {
    const actual = new Set(
      [
        new ExactScorer(),
        new AliasScorer(),
        new PatternTypeScorer(),
        new ProfileScorer(),
        new FuzzyNameScorer(),
        new InitialismScorer(),
        new LLMScorer(),
      ].map((s) => s.name),
    );
    expect([...SCORER_NAMES].sort()).toEqual([...actual].sort());
  });

  it("SCORER_KERNELS is a subset of SCORER_NAMES", () => {
    for (const k of SCORER_KERNELS) expect(SCORER_NAMES.has(k)).toBe(true);
  });

  it("the deferred (non-kernel) scorers are exactly Alias + LLM", () => {
    const deferred = [...SCORER_NAMES].filter((n) => !SCORER_KERNELS.has(n)).sort();
    expect(deferred).toEqual(["AliasScorer", "LLMScorer"]);
  });

  it("mirrors the Python SCORER_KERNELS set", () => {
    expect([...SCORER_KERNELS].sort()).toEqual([
      "ExactScorer",
      "FuzzyNameScorer",
      "InitialismScorer",
      "PatternTypeScorer",
      "ProfileScorer",
    ]);
  });
});
