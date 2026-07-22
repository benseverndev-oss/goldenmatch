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

/**
 * Parallel `(form, canonical)` EDGE arrays for injecting into the score-wasm
 * `given_name_aliased_jw` kernel (`set_name_aliases`). Flattened from the SAME
 * `form -> Set<canonical>` map `buildState` builds for the pure path — so
 * fs-core's `AliasTable::from_forms` regroups them into the identical
 * form→canonical-set structure, and `are_equivalent` is byte-parity with
 * `areEquivalent` below (set-intersection + the reflexive normalize-collide
 * shortcut, which fs-core also carries). `null` when the table is unavailable.
 */
export function aliasInjectionEdges(): {
  forms: string[];
  canonicals: string[];
} | null {
  const state = load();
  if (state === null) return null;
  const forms: string[] = [];
  const canonicals: string[] = [];
  for (const [form, cset] of state.canonicals) {
    for (const c of cset) {
      forms.push(form);
      canonicals.push(c);
    }
  }
  return forms.length === 0 ? null : { forms, canonicals };
}

/**
 * *A* canonical formal name for `name` — port of `given_names.canonical_form`.
 * Lex-first (`min`) when the form belongs to multiple canonicals ("kate" →
 * "catherine"). OOV / unavailable → the normalized input; empty → `""`; `null`
 * → `null`. Used by the `alias_match` scorer's given-name half.
 */
export function canonicalForm(name: string | null): string | null {
  if (name === null) return null;
  const norm = normalize(name);
  if (!norm) return "";
  const state = load();
  if (state === null) return norm;
  const cset = state.canonicals.get(norm);
  if (cset === undefined) return norm;
  // Lex-first, matching Python `min(canon_set)`.
  let best: string | undefined;
  for (const c of cset) if (best === undefined || c < best) best = c;
  return best ?? norm;
}

/**
 * Parallel `(normalized, canonical)` arrays for injecting into the score-wasm
 * `alias_match` kernel (`set_given_name_canonicals`). Each `normalized[i]` maps
 * to `canonicals[i]` = the pre-resolved lex-first `min(canonical set)` — the
 * SAME resolution `canonicalForm` applies, done host-side so the kernel needs
 * only a normalize + lookup. `null` when the table is unavailable.
 */
export function canonicalInjectionPairs(): {
  normalized: string[];
  canonicals: string[];
} | null {
  const state = load();
  if (state === null) return null;
  const normalized: string[] = [];
  const canonicals: string[] = [];
  for (const [form, cset] of state.canonicals) {
    let best: string | undefined;
    for (const c of cset) if (best === undefined || c < best) best = c;
    if (best !== undefined) {
      normalized.push(form);
      canonicals.push(best);
    }
  }
  return normalized.length === 0 ? null : { normalized, canonicals };
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
