/**
 * Emit Cube (cube.dev) semantic-layer declarations (wedge B).
 *
 * Emit valid Cube YAML declaring the GoldenMatch-resolved key as a conformed
 * join: a crosswalk cube keyed on the source PK + a `many_to_one` join from the
 * source cube to it, with provenance in `meta.goldenmatch`. Faithful port of the
 * emit half of Python `semantic/cube.py`; byte-for-byte YAML via `dumpYaml`.
 *
 * Member refs use single braces `{CUBE}.fk = {other.pk}` (the snake_case YAML
 * data model). The `${...}` template form is JS-models only.
 */

import { type YamlValue, dumpYaml, pyFloat } from "./yamlEmit.js";
import { pyRound6 } from "./crosswalk.js";
import type { ResolvedCrosswalk } from "./crosswalk.js";
import { type LoadedDoc, asList, asStr, asStrStripped, isObj } from "./parseUtil.js";
import {
  certificateVerdict,
  certifyKeyIntegrity,
  resolveKeyIntegrity,
  type CertificateVerdictLike,
  type KeyIntegrityCertificate,
} from "./keyIntegrity.js";
import type { SemanticFrame, SemanticFrames } from "./frame.js";
import { metricAwareAttributes, frameColumns } from "./blocking.js";
import type { SemanticFieldRoles } from "./blocking.js";

/** An optional key-integrity certificate whose trust verdict rides in
 * `meta.goldenmatch.key_integrity`. A full `KeyIntegrityCertificate` yields the
 * complete verdict; a stats-only object degrades gracefully (see
 * `certificateVerdict`). Retained for back-compat — `CertificateVerdictLike` is
 * the superset the emitter reads. */
export interface CubeKeyIntegrityCertificateLike {
  estimate?: number | null;
  maxFanOut?: number | null;
  undercountEstimate?: number | null;
}

export interface EmitCubeFromCrosswalkOptions {
  sourceCube: string;
  sourceJoinColumn?: string;
  crosswalkCube?: string;
  crosswalkSqlTable?: string | null;
  resolvedField?: string;
  certificate?: CertificateVerdictLike | null;
}

/**
 * Emit valid Cube YAML for a `ResolvedCrosswalk`: a crosswalk cube keyed on
 * `source_pk` (a `primary_key` dimension), plus a `many_to_one` join from the
 * source cube to it — so metrics group by the conformed `resolved_entity_id`.
 * GoldenMatch provenance (+ an optional certificate) rides in the crosswalk
 * cube's `meta.goldenmatch`.
 */
export function emitCubeFromCrosswalk(
  crosswalk: ResolvedCrosswalk,
  opts: EmitCubeFromCrosswalkOptions,
): string {
  const srcPk = crosswalk.sourcePkColumn ?? "source_pk";
  const resolvedField = opts.resolvedField || crosswalk.resolvedKey || "resolved_entity_id";
  const joinCol = opts.sourceJoinColumn || srcPk;
  const crosswalkCube = opts.crosswalkCube ?? "crosswalk";

  // The crosswalk cube: source_pk (primary key), source, resolved_field — dims.
  const xwDimensions: YamlValue[] = [
    { name: srcPk, sql: srcPk, type: "string", primary_key: true },
    { name: "source", sql: "source", type: "string" },
    { name: resolvedField, sql: resolvedField, type: "string" },
  ];

  const gm: { [k: string]: YamlValue } = {
    generated_by: "goldenmatch.semantic",
    resolved_key: resolvedField,
    n_records: crosswalk.nRecords ?? null,
    n_entities: crosswalk.nEntities ?? null,
    reduction_ratio: pyFloat(pyRound6(crosswalk.reductionRatio ?? 0.0)),
  };
  if (opts.certificate != null) {
    gm["key_integrity"] = certificateVerdict(opts.certificate) as YamlValue;
  }

  // emit_cube key order: name, (sql_table|sql), joins, dimensions, measures, meta.
  const xwCube: { [k: string]: YamlValue } = { name: crosswalkCube };
  if (opts.crosswalkSqlTable) xwCube["sql_table"] = opts.crosswalkSqlTable;
  xwCube["dimensions"] = xwDimensions;
  xwCube["meta"] = { goldenmatch: gm };

  // The source cube declares the conformed join to the crosswalk (many source
  // rows -> one crosswalk row on the source PK).
  const srcCube: { [k: string]: YamlValue } = {
    name: opts.sourceCube,
    joins: [
      {
        name: crosswalkCube,
        relationship: "many_to_one",
        sql: `{CUBE}.${joinCol} = {${crosswalkCube}.${srcPk}}`,
      },
    ],
  };

  return dumpYaml({ cubes: [srcCube, xwCube] });
}

// --- consume (parse) + certify (wedge A, structural tier) --------------------

/** relationship enum + legacy aliases -> modern form (direction: declaring cube first). */
const RELATIONSHIPS = new Set(["one_to_one", "one_to_many", "many_to_one"]);
const RELATIONSHIP_ALIASES: Record<string, string> = {
  has_one: "one_to_one",
  hasone: "one_to_one",
  has_many: "one_to_many",
  hasmany: "one_to_many",
  belongs_to: "many_to_one",
  belongsto: "many_to_one",
};

function normalizeRelationship(rel: unknown): string {
  const r = asStrStripped(rel);
  const key = r.toLowerCase().replace(/-/g, "_");
  if (RELATIONSHIPS.has(key)) return key;
  return RELATIONSHIP_ALIASES[key] ?? r;
}

export interface CubeDimension {
  name: string;
  sql: string; // the SQL expression — usually the column
  type: string; // string / number / time / boolean / geo
  primaryKey: boolean;
}

export interface CubeMeasure {
  name: string;
  type: string; // count / sum / avg / count_distinct / ...
  sql: string | null; // not required for `count`
}

export interface ParsedCubeJoin {
  name: string; // the joined cube's name
  relationship: string;
  sql: string; // the ON condition, e.g. "{CUBE}.fk = {other.id}"
}

/** A fully-parsed Cube (dimensions/measures/joins/meta), faithful to Python's
 * `Cube` dataclass — so a whole existing Cube model can be consumed, not just its
 * join keys. */
export interface ParsedCube {
  name: string;
  sqlTable: string | null;
  sql: string | null; // a SELECT used instead of a table
  dimensions: CubeDimension[];
  measures: CubeMeasure[];
  joins: ParsedCubeJoin[];
  meta: Record<string, unknown> | null;
}

function parseDimension(d: Record<string, unknown>): CubeDimension {
  return {
    name: asStr(d["name"]),
    sql: asStr("sql" in d ? d["sql"] : d["name"]),
    type: "type" in d ? asStr(d["type"]) : "string",
    primaryKey: Boolean(d["primary_key"]),
  };
}

function parseMeasure(m: Record<string, unknown>): CubeMeasure {
  const sql = m["sql"];
  return {
    name: asStr(m["name"]),
    type: "type" in m ? asStr(m["type"]) : "count",
    sql: typeof sql === "string" ? sql : null,
  };
}

/**
 * Parse a Cube data model's top-level `cubes:` list into full `ParsedCube`s.
 * Faithful port of Python `parse_cube_models` (consume half); operates on an
 * already-loaded document. (`views:` are a consumption re-projection with no keys
 * of their own and are intentionally not modeled.)
 */
export function parseCubeModels(doc: LoadedDoc): ParsedCube[] {
  const out: ParsedCube[] = [];
  for (const c of asList(doc["cubes"])) {
    if (!isObj(c)) continue;
    const joins: ParsedCubeJoin[] = [];
    for (const j of asList(c["joins"])) {
      if (!isObj(j)) continue;
      joins.push({
        name: asStr(j["name"]),
        relationship: normalizeRelationship(j["relationship"]),
        sql: asStr(j["sql"]),
      });
    }
    out.push({
      name: asStr(c["name"]),
      sqlTable: typeof c["sql_table"] === "string" ? (c["sql_table"] as string) : null,
      sql: typeof c["sql"] === "string" ? (c["sql"] as string) : null,
      dimensions: asList(c["dimensions"]).filter(isObj).map((d) => parseDimension(d as Record<string, unknown>)),
      measures: asList(c["measures"]).filter(isObj).map((m) => parseMeasure(m as Record<string, unknown>)),
      joins,
      meta: isObj(c["meta"]) ? (c["meta"] as Record<string, unknown>) : null,
    });
  }
  return out;
}

// Member refs are asymmetric: `{CUBE}.col` (self, column OUTSIDE the braces) and
// `{other_cube.member}` (column INSIDE). Capture the braced token + optional
// trailing `.column`. Mirrors Python's `_MEMBER_REF` (`\{([^}]+)\}…`) but the
// inner class also excludes `{` (`[^{}]+`), so a run of `{{{{…` with no closing
// brace can't trigger the O(n^2) backtracking of `[^}]+` (a linear-time,
// ReDoS-safe rewrite). Cube member refs never nest braces (`{CUBE}` /
// `{cube.member}`), so this is byte-identical to Python on every real + fixture
// input; the two diverge only on the never-emitted nested-brace pathology.
const MEMBER_REF = /\{([^{}]+)\}(?:\.(\w+))?/g;

/** Pull `{CUBE}.col` / `{other.col}` member refs out of a join `sql`, as
 * `[cubeOrNull, column]` — `{CUBE}` (self) yields `[null, col]`. */
function memberRefs(sql: string): Array<[string | null, string]> {
  const refs: Array<[string | null, string]> = [];
  for (const m of sql.matchAll(MEMBER_REF)) {
    const inside = (m[1] ?? "").trim();
    const after = m[2];
    if (inside.includes(".")) {
      const idx = inside.lastIndexOf(".");
      const head = inside.slice(0, idx).trim();
      const col = inside.slice(idx + 1).trim();
      refs.push([head === "CUBE" ? null : head, col]);
    } else if (after) {
      refs.push([inside === "CUBE" ? null : inside, after.trim()]);
    }
  }
  return refs;
}

export interface CubeJoinKey {
  fromCube: string;
  toCube: string;
  relationship: string;
  fromColumns: string[];
  toColumns: string[];
  sql: string;
}

/** The keys a cube's joins ride on — the identity its metrics depend on. Mirrors
 * Python `cube_join_keys`. */
export function cubeJoinKeys(cube: ParsedCube): CubeJoinKey[] {
  const out: CubeJoinKey[] = [];
  for (const j of cube.joins) {
    const fromColumns: string[] = [];
    const toColumns: string[] = [];
    for (const [refCube, col] of memberRefs(j.sql)) {
      if (refCube === null || refCube === cube.name) fromColumns.push(col);
      else if (refCube === j.name) toColumns.push(col);
    }
    out.push({
      fromCube: cube.name,
      toCube: j.name,
      relationship: j.relationship,
      fromColumns,
      toColumns,
      sql: j.sql,
    });
  }
  return out;
}

export interface CertifiedJoin {
  fromCube: string;
  toCube: string;
  key: string[];
  certificate: KeyIntegrityCertificate;
}

/**
 * For each join in a Cube model, certify the ONE-side key it joins on (structural
 * tier) using the key-integrity certifier — certifying exactly the identity the
 * metrics depend on. The one-side depends on direction: `many_to_one`/`one_to_one`
 * → the joined (`to`) cube; `one_to_many` → the declaring (`from`) cube (its key
 * must be unique). A join whose one-side frame is absent or whose one-side columns
 * can't be parsed is skipped. Mirrors Python `certify_cube_joins` (`resolve=false`
 * structural path — {@link certifyCubeJoinsResolved} is the ER fragmentation tier).
 */
export function certifyCubeJoins(doc: LoadedDoc, frames: SemanticFrames): CertifiedJoin[] {
  const out: CertifiedJoin[] = [];
  for (const cube of parseCubeModels(doc)) {
    for (const jk of cubeJoinKeys(cube)) {
      const [oneCube, oneColumns] =
        jk.relationship === "one_to_many"
          ? [jk.fromCube, jk.fromColumns]
          : [jk.toCube, jk.toColumns];
      const df = frames[oneCube];
      if (df === undefined || oneColumns.length === 0) continue;
      const cert = certifyKeyIntegrity(df, { key: oneColumns });
      out.push({ fromCube: jk.fromCube, toCube: jk.toCube, key: [...oneColumns], certificate: cert });
    }
  }
  return out;
}

/**
 * Async resolve-tier counterpart of {@link certifyCubeJoins}: certifies each
 * join's one-side key AND runs entity resolution on that frame's attributes to
 * measure fragmentation / undercount. Mirrors Python `certify_cube_joins(...,
 * resolve=True, roles=...)`. Pass `roles` (from `semanticFieldRoles`) to make the
 * ER metric-aware — it resolves on the model's declared dimensions and excludes
 * measures; `null` (the default) is blind selection (all columns except the key).
 * Fail-open per key.
 */
export async function certifyCubeJoinsResolved(
  doc: LoadedDoc,
  frames: SemanticFrames,
  roles: SemanticFieldRoles | null = null,
): Promise<CertifiedJoin[]> {
  const out: CertifiedJoin[] = [];
  for (const cube of parseCubeModels(doc)) {
    for (const jk of cubeJoinKeys(cube)) {
      const [oneCube, oneColumns] =
        jk.relationship === "one_to_many"
          ? [jk.fromCube, jk.fromColumns]
          : [jk.toCube, jk.toColumns];
      const df = frames[oneCube];
      if (df === undefined || oneColumns.length === 0) continue;
      const attributes = roles !== null ? metricAwareAttributes(roles, frameColumns(df)) : undefined;
      const cert = await resolveKeyIntegrity(df, {
        key: oneColumns,
        ...(attributes !== undefined ? { attributes } : {}),
      });
      out.push({ fromCube: jk.fromCube, toCube: jk.toCube, key: [...oneColumns], certificate: cert });
    }
  }
  return out;
}

// --- emit a full ParsedCube back to YAML (round-trips with parseCubeModels) ---

function emitCubeDimension(d: CubeDimension): { [k: string]: YamlValue } {
  const out: { [k: string]: YamlValue } = { name: d.name, sql: d.sql, type: d.type };
  if (d.primaryKey) out["primary_key"] = true;
  return out;
}

function emitCubeMeasure(m: CubeMeasure): { [k: string]: YamlValue } {
  const out: { [k: string]: YamlValue } = { name: m.name, type: m.type };
  if (m.sql !== null) out["sql"] = m.sql;
  return out;
}

function emitCubeJoin(j: ParsedCubeJoin): { [k: string]: YamlValue } {
  return { name: j.name, relationship: normalizeRelationship(j.relationship), sql: j.sql };
}

/** emit_cube key order: name, (sql_table | sql), joins, dimensions, measures, meta. */
function emitCube(cube: ParsedCube): { [k: string]: YamlValue } {
  const out: { [k: string]: YamlValue } = { name: cube.name };
  if (cube.sqlTable) out["sql_table"] = cube.sqlTable;
  else if (cube.sql) out["sql"] = cube.sql;
  if (cube.joins.length) out["joins"] = cube.joins.map(emitCubeJoin);
  if (cube.dimensions.length) out["dimensions"] = cube.dimensions.map(emitCubeDimension);
  if (cube.measures.length) out["measures"] = cube.measures.map(emitCubeMeasure);
  if (cube.meta) out["meta"] = cube.meta as YamlValue;
  return out;
}

/**
 * Render `ParsedCube`(s) as a valid Cube data model — a top-level `cubes:` list.
 * `parseCubeModels(emitCubeYaml(cubes))` round-trips. Mirrors Python
 * `emit_cube_yaml`.
 */
export function emitCubeYaml(cubes: ParsedCube | ParsedCube[]): string {
  const list = Array.isArray(cubes) ? cubes : [cubes];
  return dumpYaml({ cubes: list.map(emitCube) });
}
