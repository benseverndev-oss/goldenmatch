// Exact name scorer — case-insensitive exact field name match.
// Mirrors infermap/scorers/exact.py.
import type { FieldInfo, ScorerResult } from "../types.js";
import { makeScorerResult } from "../types.js";
import type { Scorer } from "./base.js";

export class ExactScorer implements Scorer {
  readonly name = "ExactScorer";
  readonly weight = 1.0;

  score(source: FieldInfo, target: FieldInfo): ScorerResult {
    const src = source.name.trim().toLowerCase();
    const tgt = target.name.trim().toLowerCase();
    if (src === tgt) {
      return makeScorerResult(1.0, `Exact name match: '${source.name}'`);
    }
    return makeScorerResult(
      0.0,
      `No exact match: '${source.name}' vs '${target.name}'`
    );
  }
}
