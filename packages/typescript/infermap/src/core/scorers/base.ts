// Scorer interface. Mirrors infermap/scorers/base.py (Protocol).
// Returning null means "abstain" — the scorer has no opinion on this pair
// and is excluded from weighted averaging. Return a ScorerResult with
// score === 0 to express a real negative (counted in the average).
import type { FieldInfo, ScorerResult } from "../types.js";

export interface Scorer {
  readonly name: string;
  readonly weight: number;
  score(source: FieldInfo, target: FieldInfo): ScorerResult | null;
}
