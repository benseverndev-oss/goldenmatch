// Default scorer list and a helper for defining function-style scorers.
// Mirrors infermap/scorers/__init__.py.
import type { FieldInfo, ScorerResult } from "../types.js";
import type { Scorer } from "./base.js";
import { ExactScorer } from "./exact.js";
import { AliasScorer } from "./alias.js";
import { PatternTypeScorer } from "./pattern-type.js";
import { ProfileScorer } from "./profile.js";
import { FuzzyNameScorer } from "./fuzzy-name.js";
import { InitialismScorer } from "./initialism.js";
import { LLMScorer } from "./llm.js";

// --- Cross-language parity surface (parity/infermap.yaml) -------------------
// The full set of built-in scorer identities (each scorer class's `.name`),
// mirrored 1:1 by the Python registry (infermap/scorers/__init__.py SCORER_NAMES)
// and enforced by the api_parity `scorers` surface. Kept in sync with the class
// `.name` literals by scorers.test.ts (asserts against instantiated scorers).
export const SCORER_NAMES: ReadonlySet<string> = new Set([
  "AliasScorer",
  "ExactScorer",
  "FuzzyNameScorer",
  "InitialismScorer",
  "LLMScorer",
  "PatternTypeScorer",
  "ProfileScorer",
]);

// The scorers backed by an `infermap-core` Rust kernel (native + wasm) — the
// reference fast path. Mirrors Python `SCORER_KERNELS`; every scorer NOT here is
// a pure-language fallback classified in parity/infermap.yaml
// scorer_kernels_deferred (the check_scorer_coverage floor). The TS WASM side is
// the InfermapBackend methods (core/wasm/backend.ts): exact/fuzzyName/initialism/
// profile/patternMatchTypes.
export const SCORER_KERNELS: ReadonlySet<string> = new Set([
  "ExactScorer",
  "FuzzyNameScorer",
  "InitialismScorer",
  "PatternTypeScorer",
  "ProfileScorer",
]);

export function defaultScorers(): Scorer[] {
  return [
    new ExactScorer(),
    new AliasScorer(),
    new PatternTypeScorer(),
    new ProfileScorer(),
    new FuzzyNameScorer(),
    new InitialismScorer(),
  ];
}

/** Build a Scorer from a plain function. Matches the Python `@scorer` decorator. */
export function defineScorer(
  name: string,
  fn: (source: FieldInfo, target: FieldInfo) => ScorerResult | null,
  weight = 1.0
): Scorer {
  return {
    name,
    weight,
    score: fn,
  };
}
