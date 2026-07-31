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

/** An optional key-integrity certificate whose stats ride in `meta.goldenmatch`. */
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
  certificate?: CubeKeyIntegrityCertificateLike | null;
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
    gm["key_integrity"] = {
      uniqueness_estimate: opts.certificate.estimate ?? null,
      max_fan_out: opts.certificate.maxFanOut ?? null,
      undercount_estimate: opts.certificate.undercountEstimate ?? null,
    };
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
