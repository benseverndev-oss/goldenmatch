// Public `map()` convenience function — dispatches to MapEngine after
// normalizing polymorphic inputs to SchemaInfo.

import type { MapEngineOptions, MapSchemasOptions } from "./engine.js";
import { MapEngine } from "./engine.js";
import type { SchemaInfo } from "./types.js";
import {
  inferSchemaFromCsvText,
  inferSchemaFromJsonText,
} from "./providers/file.js";
import { inferSchemaFromRecords } from "./providers/in-memory.js";
import { parseSchemaDefinition } from "./providers/schema-file.js";
import type { MapResult } from "./types.js";
import {
  applyScorerOverrides,
  loadEngineConfig,
  type EngineConfig,
} from "./config.js";
import { AliasScorer } from "./scorers/alias.js";
import { defaultScorers } from "./scorers/registry.js";

/**
 * Polymorphic input accepted by `map()`.
 * Edge-core knows nothing about filesystem paths or database URIs;
 * the Node wrapper (Step 15) layers those on top of this type.
 */
export type MapInput =
  | SchemaInfo
  | { records: ReadonlyArray<Record<string, unknown>>; sourceName?: string }
  | { csvText: string; sourceName?: string }
  | { jsonText: string; sourceName?: string }
  | { schemaDefinition: string | object; sourceName?: string };

export interface MapOptions extends MapSchemasOptions {
  engineOptions?: MapEngineOptions;
  /**
   * Engine config (JSON string or parsed object). Takes effect by rebuilding
   * the default scorer list with overrides + alias extensions applied.
   * Ignored if `engineOptions.scorers` is provided explicitly.
   */
  config?: string | EngineConfig;
  sampleSize?: number;
}

function isSchemaInfo(input: MapInput): input is SchemaInfo {
  return (
    typeof input === "object" &&
    input !== null &&
    "fields" in input &&
    Array.isArray((input as SchemaInfo).fields)
  );
}

/** Normalize any MapInput to a SchemaInfo. */
export function toSchemaInfo(input: MapInput, sampleSize?: number): SchemaInfo {
  if (isSchemaInfo(input)) return input;

  if ("records" in input) {
    const opts: Parameters<typeof inferSchemaFromRecords>[1] = {};
    if (input.sourceName !== undefined) opts.sourceName = input.sourceName;
    if (sampleSize !== undefined) opts.sampleSize = sampleSize;
    return inferSchemaFromRecords(input.records, opts);
  }
  if ("csvText" in input) {
    const opts: Parameters<typeof inferSchemaFromCsvText>[1] = {};
    if (input.sourceName !== undefined) opts.sourceName = input.sourceName;
    if (sampleSize !== undefined) opts.sampleSize = sampleSize;
    return inferSchemaFromCsvText(input.csvText, opts);
  }
  if ("jsonText" in input) {
    const opts: Parameters<typeof inferSchemaFromJsonText>[1] = {};
    if (input.sourceName !== undefined) opts.sourceName = input.sourceName;
    if (sampleSize !== undefined) opts.sampleSize = sampleSize;
    return inferSchemaFromJsonText(input.jsonText, opts);
  }
  if ("schemaDefinition" in input) {
    return parseSchemaDefinition(
      input.schemaDefinition as string,
      input.sourceName ?? "schema"
    );
  }

  throw new TypeError("Unrecognized MapInput shape");
}

/**
 * Convenience wrapper: extracts schemas from both inputs and runs the engine.
 */
export function map(
  source: MapInput,
  target: MapInput,
  options: MapOptions = {}
): MapResult {
  const srcSchema = toSchemaInfo(source, options.sampleSize);
  const tgtSchema = toSchemaInfo(target, options.sampleSize);

  // Build engine options, applying engine config if provided and scorers
  // weren't overridden explicitly.
  const engineOptions: MapEngineOptions = { ...options.engineOptions };
  if (options.config !== undefined && engineOptions.scorers === undefined) {
    const cfg = loadEngineConfig(options.config);
    // Rebuild the default scorer chain with config-supplied extras.
    const aliasScorer = new AliasScorer(cfg.aliases ?? {});
    const base = defaultScorers().map((sc) =>
      sc.name === "AliasScorer" ? aliasScorer : sc
    );
    engineOptions.scorers = applyScorerOverrides(base, cfg.scorers);
  }

  const engine = new MapEngine(engineOptions);
  const subOpts: MapSchemasOptions = {};
  if (options.required !== undefined) subOpts.required = options.required;
  if (options.schemaFile !== undefined) subOpts.schemaFile = options.schemaFile;
  return engine.mapSchemas(srcSchema, tgtSchema, subOpts);
}
