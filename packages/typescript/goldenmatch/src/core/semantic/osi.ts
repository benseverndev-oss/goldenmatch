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
import { type LoadedDoc, asList, asStr, isObj } from "./parseUtil.js";
import { certifyKeyIntegrity, resolveKeyIntegrity, type KeyIntegrityCertificate } from "./keyIntegrity.js";
import type { SemanticFrames } from "./frame.js";

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

// --- consume (parse) + certify (wedge A, structural tier) --------------------

export interface ParsedOsiRelationship {
  name: string;
  fromDataset: string; // MANY side (`from`)
  toDataset: string; // ONE side (`to`)
  fromColumns: string[]; // FK columns
  toColumns: string[]; // referenced PK/unique columns
}

export interface OsiField {
  name: string;
  expression: string; // the (ANSI_SQL) expression — usually the column
  datatype: string | null;
  isTime: boolean;
  label: string | null;
  description: string | null;
}

export interface OsiDataset {
  name: string;
  source: string | null; // physical table ref
  primaryKey: string[];
  uniqueKeys: string[][];
  fields: OsiField[];
}

export interface OsiMetric {
  name: string;
  expression: string;
  datatype: string | null;
  description: string | null;
}

/** A fully-parsed OSI model (datasets/fields/relationships/metrics/extensions),
 * faithful to Python's `OsiModel` dataclass — so a whole existing OSI model can be
 * consumed, not just its relationship keys. */
export interface ParsedOsiModel {
  name: string;
  datasets: OsiDataset[];
  relationships: ParsedOsiRelationship[];
  metrics: OsiMetric[];
  description: string | null;
  version: string;
  customExtensions: Record<string, unknown> | null;
}

function cols(v: unknown): string[] {
  if (v === undefined || v === null) return [];
  if (typeof v === "string") return [v];
  return asList(v).map((x) => String(x));
}

function optString(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

/** A field/metric `expression` is `{dialects: [{dialect, expression}]}` (or a bare
 * string). Prefer ANSI_SQL, else the first dialect. Mirrors `_read_expression`. */
function readExpression(expr: unknown): string {
  if (typeof expr === "string") return expr;
  if (isObj(expr)) {
    const dialects = asList(expr["dialects"]);
    for (const d of dialects) {
      if (isObj(d) && d["dialect"] === DEFAULT_DIALECT) return asStr(d["expression"]);
    }
    if (dialects.length && isObj(dialects[0])) {
      return asStr((dialects[0] as Record<string, unknown>)["expression"]);
    }
  }
  return "";
}

function parseDataset(d: Record<string, unknown>): OsiDataset {
  const fields: OsiField[] = [];
  for (const f of asList(d["fields"])) {
    if (!isObj(f)) continue;
    const dim = f["dimension"];
    fields.push({
      name: asStr(f["name"]),
      expression: readExpression(f["expression"]),
      datatype: optString(f["datatype"]),
      isTime: isObj(dim) ? Boolean(dim["is_time"]) : false,
      label: optString(f["label"]),
      description: optString(f["description"]),
    });
  }
  const pkRaw = d["primary_key"];
  const primaryKey = typeof pkRaw === "string" ? [pkRaw] : asList(pkRaw).map(String);
  const uniqueKeys = asList(d["unique_keys"]).map((k) =>
    Array.isArray(k) ? k.map(String) : cols(k),
  );
  return {
    name: asStr(d["name"]),
    source: optString(d["source"]),
    primaryKey,
    uniqueKeys,
    fields,
  };
}

/**
 * Parse an OSI/Ossie document's `semantic_model` list into full `ParsedOsiModel`s.
 * Faithful port of Python `parse_osi_models` (consume half); operates on a loaded
 * document.
 */
export function parseOsiModels(doc: LoadedDoc): ParsedOsiModel[] {
  const out: ParsedOsiModel[] = [];
  const version = "version" in doc ? asStr(doc["version"]) : OSI_VERSION;
  for (const sm of asList(doc["semantic_model"])) {
    if (!isObj(sm)) continue;
    const relationships: ParsedOsiRelationship[] = [];
    for (const r of asList(sm["relationships"])) {
      if (!isObj(r)) continue;
      relationships.push({
        name: asStr(r["name"]),
        fromDataset: asStr(r["from"]),
        toDataset: asStr(r["to"]),
        fromColumns: cols(r["from_columns"]),
        toColumns: cols(r["to_columns"]),
      });
    }
    const metrics: OsiMetric[] = [];
    for (const m of asList(sm["metrics"])) {
      if (!isObj(m)) continue;
      metrics.push({
        name: asStr(m["name"]),
        expression: readExpression(m["expression"]),
        datatype: optString(m["datatype"]),
        description: optString(m["description"]),
      });
    }
    out.push({
      name: asStr(sm["name"]),
      datasets: asList(sm["datasets"]).filter(isObj).map((d) => parseDataset(d as Record<string, unknown>)),
      relationships,
      metrics,
      description: optString(sm["description"]),
      version,
      customExtensions: isObj(sm["custom_extensions"]) ? (sm["custom_extensions"] as Record<string, unknown>) : null,
    });
  }
  return out;
}

export interface OsiJoinKey {
  dataset: string;
  columns: string[];
  side: "one" | "many";
  relationship: string;
}

/** The keys the model's relationships join on. Mirrors Python `osi_join_keys`. */
export function osiJoinKeys(model: ParsedOsiModel): OsiJoinKey[] {
  const out: OsiJoinKey[] = [];
  for (const r of model.relationships) {
    out.push({ dataset: r.toDataset, columns: r.toColumns, side: "one", relationship: r.name });
    out.push({ dataset: r.fromDataset, columns: r.fromColumns, side: "many", relationship: r.name });
  }
  return out;
}

export interface CertifiedRelationship {
  relationship: string;
  dataset: string;
  key: string[];
  certificate: KeyIntegrityCertificate;
}

/**
 * For each relationship in an OSI model, certify the ONE-side key it joins on
 * (the referenced PK) — the identity the metrics depend on (structural tier).
 * Datasets without a supplied frame are skipped. Uses the FIRST model, mirroring
 * Python `certify_osi_relationships` (`resolve=false` structural path;
 * {@link certifyOsiRelationshipsResolved} is the ER fragmentation tier).
 */
export function certifyOsiRelationships(doc: LoadedDoc, frames: SemanticFrames): CertifiedRelationship[] {
  const models = parseOsiModels(doc);
  if (!models.length) return [];
  const model = models[0]!;
  const out: CertifiedRelationship[] = [];
  for (const rel of model.relationships) {
    const df = frames[rel.toDataset];
    if (df === undefined || rel.toColumns.length === 0) continue;
    const cert = certifyKeyIntegrity(df, { key: rel.toColumns });
    out.push({ relationship: rel.name, dataset: rel.toDataset, key: [...rel.toColumns], certificate: cert });
  }
  return out;
}

/**
 * Async resolve-tier counterpart of {@link certifyOsiRelationships}: certifies
 * each relationship's one-side key AND runs entity resolution on that dataset's
 * frame to measure fragmentation / undercount. Mirrors Python
 * `certify_osi_relationships(..., resolve=True)` — blind attribute selection (all
 * columns except the key). Fail-open per key.
 */
export async function certifyOsiRelationshipsResolved(
  doc: LoadedDoc,
  frames: SemanticFrames,
): Promise<CertifiedRelationship[]> {
  const models = parseOsiModels(doc);
  if (!models.length) return [];
  const model = models[0]!;
  const out: CertifiedRelationship[] = [];
  for (const rel of model.relationships) {
    const df = frames[rel.toDataset];
    if (df === undefined || rel.toColumns.length === 0) continue;
    const cert = await resolveKeyIntegrity(df, { key: rel.toColumns });
    out.push({ relationship: rel.name, dataset: rel.toDataset, key: [...rel.toColumns], certificate: cert });
  }
  return out;
}

// --- emit a full ParsedOsiModel back to YAML (round-trips with parseOsiModels) -

/** emit_osi_dataset order: name, (source), (primary_key), (unique_keys), (fields). */
function emitOsiDataset(ds: OsiDataset): { [k: string]: YamlValue } {
  const out: { [k: string]: YamlValue } = { name: ds.name };
  if (ds.source) out["source"] = ds.source;
  if (ds.primaryKey.length) out["primary_key"] = [...ds.primaryKey];
  if (ds.uniqueKeys.length) out["unique_keys"] = ds.uniqueKeys.map((k) => [...k]);
  if (ds.fields.length) out["fields"] = ds.fields.map((f) => emitOsiField(f));
  return out;
}

/** No `cardinality` key — direction encodes it (from=many, to=one). */
function emitOsiRelationship(r: ParsedOsiRelationship): { [k: string]: YamlValue } {
  return {
    name: r.name,
    from: r.fromDataset,
    to: r.toDataset,
    from_columns: [...r.fromColumns],
    to_columns: [...r.toColumns],
  };
}

/** emit_osi_model order: name, (description), (datasets), (relationships),
 * (metrics), (custom_extensions). */
function emitOsiModel(model: ParsedOsiModel): { [k: string]: YamlValue } {
  const out: { [k: string]: YamlValue } = { name: model.name };
  if (model.description) out["description"] = model.description;
  if (model.datasets.length) out["datasets"] = model.datasets.map(emitOsiDataset);
  if (model.relationships.length) out["relationships"] = model.relationships.map(emitOsiRelationship);
  if (model.metrics.length) {
    out["metrics"] = model.metrics.map((m) => ({
      name: m.name,
      expression: dialectExpression(m.expression),
      ...(m.datatype ? { datatype: m.datatype } : {}),
    }));
  }
  if (model.customExtensions) out["custom_extensions"] = model.customExtensions as YamlValue;
  return out;
}

/**
 * Render `ParsedOsiModel`(s) as a valid OSI document — top-level `version` +
 * `semantic_model` list. `parseOsiModels(emitOsiYaml(models))` round-trips.
 * Mirrors Python `emit_osi_yaml` (which stamps `version` independently of each
 * model's parsed version — default `OSI_VERSION`).
 */
export function emitOsiYaml(
  models: ParsedOsiModel | ParsedOsiModel[],
  version: string = OSI_VERSION,
): string {
  const list = Array.isArray(models) ? models : [models];
  return dumpYaml({ version, semantic_model: list.map(emitOsiModel) });
}
