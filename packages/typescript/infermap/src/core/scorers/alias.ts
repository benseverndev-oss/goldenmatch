// Alias scorer — matches fields that are known synonyms of each other.
// Mirrors infermap/scorers/alias.py.
//
// The module-level DEFAULT_ALIASES is seeded from the shipped `generic`
// domain. The scorer supports two modes:
//
//   * `new AliasScorer()` or `new AliasScorer({ extra: ... })` — merges
//     extras into DEFAULT_ALIASES (backward compatible with TS callers
//     that pre-date the domain-dictionaries feature).
//   * `new AliasScorer({ aliases: dict })` — per-instance alias dict that
//     replaces the defaults entirely. Mirrors the Python `aliases=` param
//     and is what `MapEngine({ domains: [...] })` uses internally.
import type { FieldInfo, ScorerResult } from "../types.js";
import { makeScorerResult } from "../types.js";
import { DOMAIN as GENERIC } from "../dictionaries/generic.js";
import type { Scorer } from "./base.js";

/** Default alias dict, seeded from the shipped `generic` domain. */
export const DEFAULT_ALIASES: Record<string, readonly string[]> = { ...GENERIC };

/** Build a reverse lookup: every alias (and canonical) -> canonical key. */
export function buildLookup(
  aliases: Record<string, readonly string[]>
): Map<string, string> {
  const lookup = new Map<string, string>();
  for (const [canonical, list] of Object.entries(aliases)) {
    lookup.set(canonical.trim().toLowerCase(), canonical);
    for (const alias of list) {
      lookup.set(alias.trim().toLowerCase(), canonical);
    }
  }
  return lookup;
}

export interface AliasScorerOptions {
  /** Per-instance alias dict that REPLACES defaults. */
  aliases?: Record<string, readonly string[]>;
}

export class AliasScorer implements Scorer {
  readonly name = "AliasScorer";
  readonly weight = 0.95;

  private readonly lookup: Map<string, string>;

  constructor(
    arg?: Record<string, readonly string[]> | AliasScorerOptions
  ) {
    // Two shapes supported:
    //   AliasScorer({ aliases: {...} })  -> replace defaults (new Python path)
    //   AliasScorer({ foo: [...] })      -> extras merged into defaults (legacy)
    //   AliasScorer(undefined)           -> defaults
    if (
      arg &&
      typeof arg === "object" &&
      "aliases" in arg &&
      (arg as AliasScorerOptions).aliases
    ) {
      this.lookup = buildLookup((arg as AliasScorerOptions).aliases!);
      return;
    }
    const extras = (arg ?? {}) as Record<string, readonly string[]>;
    const merged: Record<string, readonly string[]> = { ...DEFAULT_ALIASES };
    for (const [canonical, list] of Object.entries(extras)) {
      merged[canonical] = list;
    }
    this.lookup = buildLookup(merged);
  }

  private canonical(name: string): string | undefined {
    return this.lookup.get(name.trim().toLowerCase());
  }

  score(source: FieldInfo, target: FieldInfo): ScorerResult | null {
    const srcName = source.name.trim().toLowerCase();
    const tgtName = target.name.trim().toLowerCase();

    const srcCanonical = this.canonical(srcName);
    const tgtCanonical = this.canonical(tgtName);

    const declaredRaw = target.metadata["aliases"];
    const declaredAliases: string[] = Array.isArray(declaredRaw)
      ? (declaredRaw as unknown[]).filter((x): x is string => typeof x === "string")
      : [];
    const declaredLower = declaredAliases.map((a) => a.trim().toLowerCase());
    const targetHasDeclared = declaredAliases.length > 0;

    if (declaredLower.includes(srcName)) {
      return makeScorerResult(
        0.95,
        `'${source.name}' matches declared alias of target '${target.name}'`
      );
    }

    if (
      srcCanonical === undefined &&
      tgtCanonical === undefined &&
      !targetHasDeclared
    ) {
      return null;
    }

    if (srcCanonical !== undefined && srcCanonical === tgtCanonical) {
      return makeScorerResult(
        0.95,
        `'${source.name}' and '${target.name}' share canonical name '${srcCanonical}'`
      );
    }

    return makeScorerResult(
      0.0,
      `'${source.name}' (canonical=${srcCanonical ?? "None"}) and ` +
        `'${target.name}' (canonical=${tgtCanonical ?? "None"}) are different`
    );
  }
}
