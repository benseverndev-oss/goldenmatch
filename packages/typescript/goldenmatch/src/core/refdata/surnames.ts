/**
 * US Census 2010 surname-frequency lookup — edge-safe port of the subset of
 * goldenmatch/refdata/surnames.py the name_freq_weighted_jw scorer needs.
 * No node:* imports. Data from the generated censusSurnames module.
 */
import { CENSUS_SURNAMES } from "./censusSurnames.js";

interface SurnameState {
  counts: Map<string, number>;
  ranks: Map<string, number>;
  totalCount: number;
  minCount: number;
}

/** Strip non-letters + UPPERCASE. Mirrors Python surnames._normalize (isalpha().upper()). */
function normalize(name: string): string {
  return name.replace(/[^\p{L}]/gu, "").toUpperCase();
}

// undefined = not built; null = unavailable; object = loaded.
let _state: SurnameState | null | undefined;

function buildState(): SurnameState | null {
  const counts = new Map<string, number>();
  const ranks = new Map<string, number>();
  let total = 0;
  let minCount = 0;
  // Mirror Python _build_state_from_file: iterate ROWS; total/min accumulate
  // over every row (last-wins into the maps). Rows: [name, rank, count].
  for (const [rawName, rank, count] of CENSUS_SURNAMES) {
    const name = normalize(rawName);
    if (!name) continue;
    counts.set(name, count);
    ranks.set(name, rank);
    total += count;
    if (minCount === 0 || count < minCount) minCount = count;
  }
  if (counts.size === 0) return null;
  return { counts, ranks, totalCount: total, minCount: minCount || 1 };
}

function load(): SurnameState | null {
  if (_state === undefined) _state = buildState();
  return _state;
}

export function isAvailable(): boolean {
  return load() !== null;
}

export function surnameRank(name: string | null): number | null {
  if (name === null) return null;
  const state = load();
  if (state === null) return null;
  const r = state.ranks.get(normalize(name));
  return r === undefined ? null : r;
}

export function surnameIdf(name: string | null): number | null {
  if (name === null) return null;
  const state = load();
  if (state === null || state.totalCount <= 0 || state.minCount <= 0) return null;
  const c = state.counts.get(normalize(name));
  if (c === undefined) return 1.0; // OOV: rarer than anything in the table
  if (c >= state.totalCount) return 0.0;
  const numerator = Math.log(state.totalCount / c);
  const denominator = Math.log(state.totalCount / state.minCount);
  if (denominator <= 0) return 0.0;
  return Math.max(0.0, Math.min(1.0, numerator / denominator));
}
