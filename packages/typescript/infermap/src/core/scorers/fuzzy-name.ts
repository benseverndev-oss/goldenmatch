// Fuzzy name scorer — Jaro-Winkler similarity on normalized field names.
// Mirrors infermap/scorers/fuzzy_name.py.
import type { FieldInfo, ScorerResult } from "../types.js";
import { makeScorerResult } from "../types.js";
import { jaroWinklerSimilarity } from "../util/string-distance.js";
import type { Scorer } from "./base.js";
import { getInfermapBackend } from "../wasm/backend.js";

const normalize = (name: string): string =>
  name.trim().toLowerCase().replace(/[_\- ]/g, "");

export class FuzzyNameScorer implements Scorer {
  readonly name = "FuzzyNameScorer";
  readonly weight = 0.4;

  score(source: FieldInfo, target: FieldInfo): ScorerResult {
    const srcName = source.canonicalName ?? source.name;
    const tgtName = target.canonicalName ?? target.name;
    const backend = getInfermapBackend();
    const sim = backend
      ? backend.fuzzyNameScore(srcName, tgtName)
      : jaroWinklerSimilarity(normalize(srcName), normalize(tgtName));
    const srcNorm = normalize(srcName);
    const tgtNorm = normalize(tgtName);
    return makeScorerResult(
      sim,
      `Jaro-Winkler similarity between '${srcNorm}' and '${tgtNorm}': ${sim.toFixed(3)}`
    );
  }
}
