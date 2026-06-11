/**
 * Given-name alias equivalence — edge-safe port of
 * goldenmatch/refdata/given_names.py (the subset the TS scorer needs).
 * No node:* imports. Data comes from the generated givenNameAliases module.
 */
import { GIVEN_NAME_ALIASES } from "./givenNameAliases.js";

interface GivenNameState {
  /** form -> set of canonicals it belongs to (multi-canonical short forms). */
  canonicals: Map<string, Set<string>>;
}

/** Unicode-aware: strip non-letters, lowercase. Mirrors Python `_normalize`. */
function normalize(name: string): string {
  return name.replace(/[^\p{L}]/gu, "").toLowerCase();
}

// undefined = not yet built; null = unavailable; object = loaded.
let _state: GivenNameState | null | undefined;

function buildState(): GivenNameState | null {
  const canonicals = new Map<string, Set<string>>();
  const aliasesBlock =
    (GIVEN_NAME_ALIASES as { aliases?: Record<string, readonly string[]> })
      .aliases ?? {};
  for (const [rawCanonical, rawAliases] of Object.entries(aliasesBlock)) {
    const c = normalize(rawCanonical);
    if (!c) continue;
    const bucket = new Set<string>([c]);
    for (const rawAlias of rawAliases) {
      const a = normalize(rawAlias);
      if (a) bucket.add(a);
    }
    for (const form of bucket) {
      let cset = canonicals.get(form);
      if (cset === undefined) {
        cset = new Set<string>();
        canonicals.set(form, cset);
      }
      cset.add(c);
    }
  }
  return canonicals.size === 0 ? null : { canonicals };
}

function load(): GivenNameState | null {
  if (_state === undefined) _state = buildState();
  return _state;
}

export function isAvailable(): boolean {
  return load() !== null;
}

/** True iff a and b share at least one canonical. Symmetric, reflexive. */
export function areEquivalent(a: string | null, b: string | null): boolean {
  if (a === null || b === null) return false;
  const na = normalize(a);
  const nb = normalize(b);
  if (!na || !nb) return false;
  if (na === nb) return true;
  const state = load();
  if (state === null) return false;
  const ca = state.canonicals.get(na);
  const cb = state.canonicals.get(nb);
  if (ca === undefined || cb === undefined) return false;
  for (const c of ca) if (cb.has(c)) return true;
  return false;
}
