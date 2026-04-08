// JSON config loading + saved MapResult round-trip.
// Mirrors infermap/config.py but JSON-only (no YAML).
//
// Two distinct config shapes live here:
//
//   1. Engine config — scorer weight overrides + alias extensions.
//      Shape: { scorers?: { [name]: { enabled?, weight? } }, aliases?: { [canonical]: string[] } }
//      Consumed by `loadEngineConfig` and passed to MapEngine.
//
//   2. Saved MapResult config — a previously-computed mapping serialized to disk.
//      Shape: { version, mappings: [{source, target, confidence}], unmapped_source, unmapped_target }
//      Loaded by `fromConfig`, written by `mapResultToConfigJson`.

import type {
  FieldMapping,
  MapResult,
  Scorer,
} from "./index.js";

// ---------- engine config ----------

export interface ScorerOverride {
  enabled?: boolean;
  weight?: number;
}

export interface EngineConfig {
  scorers?: Record<string, ScorerOverride>;
  aliases?: Record<string, string[]>;
}

export class ConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigError";
  }
}

/** Parse + validate engine config JSON. Returns a normalized EngineConfig. */
export function loadEngineConfig(json: string | EngineConfig): EngineConfig {
  const raw: unknown = typeof json === "string" ? JSON.parse(json) : json;
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    throw new ConfigError("Engine config must be an object");
  }
  const cfg = raw as Record<string, unknown>;
  const out: EngineConfig = {};

  if (cfg["scorers"] !== undefined) {
    if (typeof cfg["scorers"] !== "object" || cfg["scorers"] === null) {
      throw new ConfigError("'scorers' must be an object");
    }
    const scorers: Record<string, ScorerOverride> = {};
    for (const [name, override] of Object.entries(cfg["scorers"])) {
      if (typeof override !== "object" || override === null) {
        throw new ConfigError(`Scorer override for '${name}' must be an object`);
      }
      const o = override as Record<string, unknown>;
      const entry: ScorerOverride = {};
      if (typeof o["enabled"] === "boolean") entry.enabled = o["enabled"];
      if (typeof o["weight"] === "number") entry.weight = o["weight"];
      scorers[name] = entry;
    }
    out.scorers = scorers;
  }

  if (cfg["aliases"] !== undefined) {
    if (typeof cfg["aliases"] !== "object" || cfg["aliases"] === null) {
      throw new ConfigError("'aliases' must be an object");
    }
    const aliases: Record<string, string[]> = {};
    for (const [canonical, list] of Object.entries(cfg["aliases"])) {
      if (!Array.isArray(list)) {
        throw new ConfigError(
          `Aliases for '${canonical}' must be an array of strings`
        );
      }
      aliases[canonical] = list.filter((x): x is string => typeof x === "string");
    }
    out.aliases = aliases;
  }

  return out;
}

/**
 * Apply scorer overrides (enable/disable + reweight) to a scorer list.
 * Returns a new array; original is untouched. Disabled scorers are dropped.
 */
export function applyScorerOverrides(
  scorers: readonly Scorer[],
  overrides: Record<string, ScorerOverride> | undefined
): Scorer[] {
  if (!overrides) return [...scorers];
  const out: Scorer[] = [];
  for (const sc of scorers) {
    const override = overrides[sc.name];
    if (!override) {
      out.push(sc);
      continue;
    }
    if (override.enabled === false) continue;
    if (override.weight !== undefined) {
      // Wrap with a new object exposing the overridden weight so we don't
      // mutate shared scorer instances.
      out.push({
        name: sc.name,
        weight: override.weight,
        score: sc.score.bind(sc),
      });
    } else {
      out.push(sc);
    }
  }
  return out;
}

// ---------- saved MapResult config ----------

export interface MapResultConfig {
  version: string;
  mappings: Array<{ source: string; target: string; confidence: number }>;
  unmapped_source?: string[];
  unmapped_target?: string[];
}

/**
 * Reconstruct a MapResult from a saved config JSON.
 * The resulting MapResult has empty breakdowns and reasoning (not serialized).
 */
export function fromConfig(json: string | MapResultConfig): MapResult {
  const raw: unknown = typeof json === "string" ? JSON.parse(json) : json;
  if (raw === null || typeof raw !== "object" || !("mappings" in raw)) {
    throw new ConfigError("Config is missing required 'mappings' key");
  }
  const data = raw as Record<string, unknown>;
  if (!Array.isArray(data["mappings"])) {
    throw new ConfigError("'mappings' must be a list");
  }

  const mappings: FieldMapping[] = [];
  for (const entry of data["mappings"]) {
    if (entry === null || typeof entry !== "object") {
      throw new ConfigError("Each mapping entry must be an object");
    }
    const e = entry as Record<string, unknown>;
    mappings.push({
      source: typeof e["source"] === "string" ? e["source"] : "",
      target: typeof e["target"] === "string" ? e["target"] : "",
      confidence: typeof e["confidence"] === "number" ? e["confidence"] : 0,
      breakdown: {},
      reasoning: "",
    });
  }

  const unmappedSource = Array.isArray(data["unmapped_source"])
    ? (data["unmapped_source"] as unknown[]).filter(
        (x): x is string => typeof x === "string"
      )
    : [];
  const unmappedTarget = Array.isArray(data["unmapped_target"])
    ? (data["unmapped_target"] as unknown[]).filter(
        (x): x is string => typeof x === "string"
      )
    : [];

  return {
    mappings,
    unmappedSource,
    unmappedTarget,
    warnings: [],
    metadata: {
      version: typeof data["version"] === "string" ? data["version"] : "",
    },
  };
}

/** Serialize a MapResult to the saved-config JSON shape. */
export function mapResultToConfigJson(result: MapResult): string {
  const round = (n: number): number => Math.round(n * 1000) / 1000;
  const data: MapResultConfig = {
    version: "1",
    mappings: result.mappings.map((m) => ({
      source: m.source,
      target: m.target,
      confidence: round(m.confidence),
    })),
    unmapped_source: result.unmappedSource,
    unmapped_target: result.unmappedTarget,
  };
  return JSON.stringify(data, null, 2);
}
