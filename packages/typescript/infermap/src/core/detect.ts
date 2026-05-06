// Lightweight domain-pack auto-detection from column names.
import { listDomains, loadDomain } from "goldencheck-types";

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
  let columns: string[];
  if ("columns" in input && Array.isArray(input.columns)) {
    columns = input.columns;
  } else if ("records" in input && input.records && input.records.length > 0) {
    columns = Object.keys(input.records[0]!);
  } else {
    return null;
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
    const score = hits / Math.max(columns.length, 1);
    scored.push([d, score]);
  }

  if (scored.length === 0) return null;
  const bestScore = scored.reduce((m, [, s]) => (s > m ? s : m), 0);
  if (bestScore < minScore) return null;
  const top = scored.filter(([, s]) => s === bestScore).map(([d]) => d);
  // Tie-break: refuse to pick rather than silently choosing one.
  return top.length === 1 ? top[0]! : null;
}
