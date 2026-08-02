/**
 * Emit dbt / MetricFlow semantic-model entity declarations (wedge B).
 *
 * Given a GoldenMatch-resolved key, generate the `semantic_models` YAML that
 * declares that conformed key as the PRIMARY entity — so every metric joins on
 * resolved identity. Faithful port of the emit half of Python
 * `semantic/metricflow.py`; the byte-for-byte YAML is produced by `dumpYaml`
 * (matching `yaml.safe_dump(sort_keys=False, default_flow_style=False)`).
 */

import { type YamlValue, dumpYaml } from "./yamlEmit.js";
import type { ResolvedCrosswalk } from "./crosswalk.js";
import { type LoadedDoc, asList, asStrStripped, isObj } from "./parseUtil.js";

const PRIMARY_ENTITY_TYPES = new Set(["primary", "natural"]);

/** One semantic model's declared identity, ready to feed `certifyKeyIntegrity`. */
export interface DeclaredKeySpec {
  model: string;
  key: string[]; // primary/natural entity column(s)
  measures: string[];
  grain: string[] | null; // default agg_time_dimension, if any
  foreignKeys: string[]; // foreign entities (join edges)
}

/** The physical column an entity maps to: `expr` if given, else `name`. */
function entityColumn(entity: Record<string, unknown>): string {
  const expr = entity["expr"];
  if (typeof expr === "string" && expr.trim()) return expr.trim();
  return asStrStripped(entity["name"]);
}

function measureColumn(measure: Record<string, unknown>): string {
  const expr = measure["expr"];
  if (typeof expr === "string" && expr.trim()) return expr.trim();
  return asStrStripped(measure["name"]);
}

/**
 * Parse dbt/MetricFlow `semantic_models` into `DeclaredKeySpec`s — one per model
 * that declares a primary/natural entity (models without one are skipped;
 * nothing to certify). Faithful port of Python `parse_semantic_models` (consume
 * half); operates on an already-loaded document object.
 */
export function parseSemanticModels(doc: LoadedDoc): DeclaredKeySpec[] {
  const specs: DeclaredKeySpec[] = [];
  for (const sm of asList(doc["semantic_models"])) {
    if (!isObj(sm)) continue;
    const name = asStrStripped(sm["name"]);

    const primaryKey: string[] = [];
    let foreignKeys: string[] = [];
    const uniqueKeys: string[] = [];
    for (const ent of asList(sm["entities"])) {
      if (!isObj(ent)) continue;
      const etype = asStrStripped(ent["type"]).toLowerCase();
      const col = entityColumn(ent);
      if (!col) continue;
      if (PRIMARY_ENTITY_TYPES.has(etype)) primaryKey.push(col);
      else if (etype === "foreign") foreignKeys.push(col);
      else if (etype === "unique") {
        uniqueKeys.push(col);
        // A unique (non-primary) entity is a join edge when a primary key is
        // present; it is promoted to the key only when no primary is.
        foreignKeys.push(col);
      }
    }

    const key = primaryKey.length ? primaryKey : uniqueKeys;
    if (!key.length) continue; // nothing to certify for this model
    if (!primaryKey.length) {
      // Promoted a unique key to the primary role — drop it from foreign_keys.
      foreignKeys = foreignKeys.filter((c) => !uniqueKeys.includes(c));
    }

    const measures: string[] = [];
    for (const m of asList(sm["measures"])) {
      if (!isObj(m)) continue;
      const c = measureColumn(m);
      if (c) measures.push(c);
    }

    let grain: string[] | null = null;
    const defaults = sm["defaults"];
    const aggTime = isObj(defaults) ? defaults["agg_time_dimension"] : undefined;
    if (typeof aggTime === "string" && aggTime.trim()) grain = [aggTime.trim()];

    specs.push({ model: name, key: [...key], measures, grain, foreignKeys });
  }
  return specs;
}

export interface EmitSemanticModelOptions {
  resolvedKey: string;
  entityName?: string;
  sourceKey?: string;
  measures?: readonly string[];
  grain?: readonly string[] | string | null;
  modelRef?: string;
  measureAgg?: string;
}

/**
 * Build ONE MetricFlow `semantic_models[]` entry declaring `resolvedKey` as the
 * PRIMARY entity — the GoldenMatch-conformed join key every metric inherits. The
 * original per-source key (`sourceKey`), if given and distinct, is declared a
 * `unique` entity so it stays queryable but is no longer the join primary.
 */
export function emitSemanticModel(
  model: string,
  opts: EmitSemanticModelOptions,
): { [k: string]: YamlValue } {
  const measureAgg = opts.measureAgg ?? "sum";
  const entities: YamlValue[] = [
    { name: opts.entityName || model, type: "primary", expr: opts.resolvedKey },
  ];
  if (opts.sourceKey && opts.sourceKey !== opts.resolvedKey) {
    entities.push({ name: opts.sourceKey, type: "unique", expr: opts.sourceKey });
  }

  const sm: { [k: string]: YamlValue } = {
    name: model,
    model: opts.modelRef || `ref('${model}')`,
    entities,
  };

  const grainList =
    typeof opts.grain === "string" ? [opts.grain] : [...(opts.grain ?? [])];
  if (grainList.length > 0) {
    sm["defaults"] = { agg_time_dimension: grainList[0]! };
  }

  const measures = opts.measures ?? [];
  if (measures.length > 0) {
    sm["measures"] = measures.map((m) => ({ name: m, agg: measureAgg, expr: m }));
  }
  return sm;
}

/**
 * Render one or more `emitSemanticModel` dicts as a `semantic_models` YAML
 * document (block style, key order preserved).
 */
export function emitMetricflowYaml(
  models: { [k: string]: YamlValue } | Array<{ [k: string]: YamlValue }>,
): string {
  const list = Array.isArray(models) ? models : [models];
  return dumpYaml({ semantic_models: list });
}

/**
 * Emit the `semantic_models` YAML for a `ResolvedCrosswalk`: its `resolvedKey`
 * becomes the primary entity, its `sourcePkColumn` the `unique` source key.
 */
export function emitFromCrosswalk(
  crosswalk: ResolvedCrosswalk,
  model: string,
  opts: {
    entityName?: string;
    measures?: readonly string[];
    grain?: readonly string[] | string | null;
    modelRef?: string;
  } = {},
): string {
  const sm = emitSemanticModel(model, {
    resolvedKey: crosswalk.resolvedKey ?? "resolved_entity_id",
    ...(opts.entityName !== undefined ? { entityName: opts.entityName } : {}),
    ...(crosswalk.sourcePkColumn !== undefined ? { sourceKey: crosswalk.sourcePkColumn } : {}),
    ...(opts.measures !== undefined ? { measures: opts.measures } : {}),
    ...(opts.grain !== undefined ? { grain: opts.grain } : {}),
    ...(opts.modelRef !== undefined ? { modelRef: opts.modelRef } : {}),
  });
  return emitMetricflowYaml(sm);
}
