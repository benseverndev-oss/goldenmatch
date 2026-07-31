/**
 * Emit Open Semantic Interchange / Apache Ossie declarations (wedge C).
 *
 * Emit valid OSI declaring the GoldenMatch-resolved key as the conformed join: a
 * crosswalk dataset keyed on the source PK, plus a relationship joining the
 * source dataset (many) to it (one), with certification/provenance in
 * `custom_extensions`. Faithful port of the emit half of Python
 * `semantic/osi.py` (spec `0.2.0.dev0`); byte-for-byte YAML via `dumpYaml`.
 *
 * Schema-faithful: the top level is `version` + a `semantic_model` LIST; a
 * relationship's direction IS its cardinality (`from` = many, `to` = one).
 */

import { type YamlValue, dumpYaml, pyFloat } from "./yamlEmit.js";
import { pyRound6 } from "./crosswalk.js";
import type { ResolvedCrosswalk } from "./crosswalk.js";
import type { CubeKeyIntegrityCertificateLike } from "./cube.js";

export const OSI_VERSION = "0.2.0.dev0";
export const DEFAULT_DIALECT = "ANSI_SQL";

function dialectExpression(expression: string, dialect: string = DEFAULT_DIALECT): YamlValue {
  return { dialects: [{ dialect, expression }] };
}

/** One OSI field spec (the subset the crosswalk emitter produces). */
interface OsiFieldSpec {
  name: string;
  expression: string;
  datatype?: string | null;
  isTime?: boolean;
  label?: string | null;
  description?: string | null;
}

/** emit_osi_field: name, expression, (datatype), dimension, (label), (description). */
function emitOsiField(f: OsiFieldSpec): { [k: string]: YamlValue } {
  const out: { [k: string]: YamlValue } = {
    name: f.name,
    expression: dialectExpression(f.expression || f.name),
  };
  if (f.datatype) out["datatype"] = f.datatype;
  out["dimension"] = { is_time: f.isTime ?? false };
  if (f.label) out["label"] = f.label;
  if (f.description) out["description"] = f.description;
  return out;
}

export interface EmitOsiFromCrosswalkOptions {
  sourceDataset: string;
  sourceJoinColumn?: string;
  crosswalkDataset?: string;
  crosswalkSource?: string | null;
  resolvedField?: string;
  certificate?: CubeKeyIntegrityCertificateLike | null;
  modelName?: string;
}

/**
 * Emit valid OSI for a `ResolvedCrosswalk`: a crosswalk dataset keyed on
 * `source_pk`, plus a relationship joining the source dataset (many) to it (one)
 * — so metrics group by the conformed `resolved_entity_id`. GoldenMatch
 * provenance (+ an optional certificate) rides in `custom_extensions`.
 */
export function emitOsiFromCrosswalk(
  crosswalk: ResolvedCrosswalk,
  opts: EmitOsiFromCrosswalkOptions,
): string {
  const srcPk = crosswalk.sourcePkColumn ?? "source_pk";
  const resolvedField = opts.resolvedField || crosswalk.resolvedKey || "resolved_entity_id";
  const joinCol = opts.sourceJoinColumn || srcPk;
  const crosswalkDataset = opts.crosswalkDataset ?? "crosswalk";

  // emit_osi_dataset order: name, (source), (primary_key), (unique_keys), (fields).
  const xwDataset: { [k: string]: YamlValue } = { name: crosswalkDataset };
  if (opts.crosswalkSource) xwDataset["source"] = opts.crosswalkSource;
  xwDataset["primary_key"] = [srcPk];
  xwDataset["fields"] = [
    emitOsiField({ name: "source", expression: "source", datatype: "String" }),
    emitOsiField({ name: srcPk, expression: srcPk }),
    emitOsiField({ name: resolvedField, expression: resolvedField, datatype: "String" }),
  ];

  const rel: { [k: string]: YamlValue } = {
    name: `${opts.sourceDataset}_to_${crosswalkDataset}`,
    from: opts.sourceDataset,
    to: crosswalkDataset,
    from_columns: [joinCol],
    to_columns: [srcPk],
  };

  const gm: { [k: string]: YamlValue } = {
    generated_by: "goldenmatch.semantic",
    resolved_key: resolvedField,
    n_records: crosswalk.nRecords ?? null,
    n_entities: crosswalk.nEntities ?? null,
    reduction_ratio: pyFloat(pyRound6(crosswalk.reductionRatio ?? 0.0)),
  };
  if (opts.certificate != null) {
    gm["key_integrity"] = {
      uniqueness_estimate: opts.certificate.estimate ?? null,
      max_fan_out: opts.certificate.maxFanOut ?? null,
      undercount_estimate: opts.certificate.undercountEstimate ?? null,
    };
  }

  // emit_osi_model order: name, (description), (datasets), (relationships),
  // (metrics), (custom_extensions).
  const model: { [k: string]: YamlValue } = {
    name: opts.modelName || `${opts.sourceDataset}_resolved`,
    datasets: [xwDataset],
    relationships: [rel],
    custom_extensions: { goldenmatch: gm },
  };

  return dumpYaml({ version: OSI_VERSION, semantic_model: [model] });
}
