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
