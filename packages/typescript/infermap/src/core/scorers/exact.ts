// Exact name scorer — case-insensitive exact field name match.
// Mirrors infermap/scorers/exact.py.
import type { FieldInfo, ScorerResult } from "../types.js";
import { makeScorerResult } from "../types.js";
import type { Scorer } from "./base.js";
import { getInfermapBackend } from "../wasm/backend.js";

export class ExactScorer implements Scorer {
  readonly name = "ExactScorer";
  readonly weight = 1.0;

  score(source: FieldInfo, target: FieldInfo): ScorerResult {
    const backend = getInfermapBackend();
    const sim = backend
      ? backend.exactScore(source.name, target.name)
      : source.name.trim().toLowerCase() === target.name.trim().toLowerCase()
        ? 1.0
        : 0.0;
    if (sim === 1.0) {
      return makeScorerResult(1.0, `Exact name match: '${source.name}'`);
    }
    return makeScorerResult(
      0.0,
      `No exact match: '${source.name}' vs '${target.name}'`
    );
  }
}
