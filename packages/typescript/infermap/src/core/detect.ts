// Lightweight domain-pack auto-detection from column names.
import { listDomains, loadDomain } from "goldencheck-types";
import type { DetectionResult } from "goldencheck-types";

export const DEFAULT_MIN_SCORE = 0.3;

const TOKEN_SPLIT = /[_\-.\s]+/;

export interface DetectInput {
  columns: string[];
}

function tokens(s: string): string[] {
  return s.toLowerCase().split(TOKEN_SPLIT).filter(Boolean);
}

/** True iff `hint`'s tokens appear as a contiguous run in `col`'s tokens.
 *  Replaces the prior symmetric `c.includes(h) || h.includes(c)`, which
 *  fired on partial overlaps — a 2-char hint like `"id"` matched
 *  `account_id`, `paid`, `void_id` indiscriminately. Token-boundary
 *  matching keeps `"npi"` matching `provider_npi` while rejecting it
 *  against `"npiece"`. */
function hintMatches(hint: string, col: string): boolean {
  const h = tokens(hint);
  const c = tokens(col);
  if (h.length === 0 || c.length === 0) return false;
  for (let i = 0; i <= c.length - h.length; i++) {
    let ok = true;
    for (let j = 0; j < h.length; j++) {
      if (c[i + j] !== h[j]) {
        ok = false;
        break;
      }
    }
    if (ok) return true;
  }
  return false;
}

export function detectDomain(
  input: DetectInput | { records?: ReadonlyArray<Record<string, unknown>> },
  candidates?: string[],
  minScore: number = DEFAULT_MIN_SCORE,
): string | null {
  return detectDomainDetailed(input, candidates, minScore).domain;
}

/** Auto-detect with full diagnostic info. See `DetectionResult` for the
 *  shape. Callers like `goldenpipe`'s infer_schema stage use this to
 *  distinguish "confident pick" from "tied" / "below threshold" /
 *  "no data" and surface that to the InferredSchema's evidence map. */
export function detectDomainDetailed(
  input: DetectInput | { records?: ReadonlyArray<Record<string, unknown>> },
  candidates?: string[],
  minScore: number = DEFAULT_MIN_SCORE,
): DetectionResult {
  let columns: string[];
  if ("columns" in input && Array.isArray(input.columns)) {
    columns = input.columns;
  } else if ("records" in input && input.records && input.records.length > 0) {
    columns = Object.keys(input.records[0]!);
  } else {
    return { domain: null, score: 0, runner_up: null, runner_up_score: 0, reason: "no_data" };
  }
  if (columns.length === 0) {
    return { domain: null, score: 0, runner_up: null, runner_up_score: 0, reason: "no_data" };
  }

  const domains = candidates ?? listDomains().filter((d) => d !== "generic");
  const scored: Array<[string, number]> = [];
  for (const d of domains) {
    const pack = loadDomain(d);
    const allHints = new Set<string>();
    for (const spec of Object.values(pack.types)) {
      for (const h of spec.name_hints) allHints.add(h);
    }
    if (allHints.size === 0) continue;

    let hits = 0;
    for (const c of columns) {
      for (const h of allHints) {
        if (hintMatches(h, c)) {
          hits++;
          break;
        }
      }
    }
    scored.push([d, hits / Math.max(columns.length, 1)]);
  }

  if (scored.length === 0) {
    return { domain: null, score: 0, runner_up: null, runner_up_score: 0, reason: "no_data" };
  }

  scored.sort((a, b) => b[1] - a[1]);
  const [bestName, bestScore] = scored[0]!;
  const runner = scored[1];
  const runnerName = runner ? runner[0] : null;
  const runnerScore = runner ? runner[1] : 0;

  if (bestScore < minScore) {
    return { domain: null, score: bestScore, runner_up: runnerName, runner_up_score: runnerScore, reason: "below_min_score" };
  }
  const topCount = scored.filter(([, s]) => s === bestScore).length;
  if (topCount > 1) {
    return { domain: null, score: bestScore, runner_up: runnerName, runner_up_score: runnerScore, reason: "tie" };
  }
  return { domain: bestName, score: bestScore, runner_up: runnerName, runner_up_score: runnerScore, reason: "confident" };
}
