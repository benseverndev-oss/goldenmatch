// Lightweight domain-pack auto-detection from column names.
import { listDomains, loadDomain } from "goldencheck-types";

export const DEFAULT_MIN_SCORE = 0.3;

export interface DetectInput {
  columns: string[];
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

  const cols_lc = columns.map((c) => String(c).toLowerCase());
  const domains = candidates ?? listDomains().filter((d) => d !== "generic");

  let best: string | null = null;
  let bestScore = 0;

  for (const d of domains) {
    const pack = loadDomain(d);
    const allHints = new Set<string>();
    for (const spec of Object.values(pack.types)) {
      for (const h of spec.name_hints) allHints.add(h.toLowerCase());
    }
    if (allHints.size === 0) continue;

    let hits = 0;
    for (const c of cols_lc) {
      for (const h of allHints) {
        if (c.includes(h) || h.includes(c)) {
          hits++;
          break;
        }
      }
    }
    const score = hits / Math.max(cols_lc.length, 1);
    if (score > bestScore) {
      best = d;
      bestScore = score;
    }
  }

  return bestScore >= minScore ? best : null;
}
