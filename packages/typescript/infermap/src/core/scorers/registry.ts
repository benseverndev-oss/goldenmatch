// Default scorer list and a helper for defining function-style scorers.
// Mirrors infermap/scorers/__init__.py.
import type { FieldInfo, ScorerResult } from "../types.js";
import type { Scorer } from "./base.js";
import { ExactScorer } from "./exact.js";
import { AliasScorer } from "./alias.js";
import { PatternTypeScorer } from "./pattern-type.js";
import { ProfileScorer } from "./profile.js";
import { FuzzyNameScorer } from "./fuzzy-name.js";

export function defaultScorers(): Scorer[] {
  return [
    new ExactScorer(),
    new AliasScorer(),
    new PatternTypeScorer(),
    new ProfileScorer(),
    new FuzzyNameScorer(),
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
