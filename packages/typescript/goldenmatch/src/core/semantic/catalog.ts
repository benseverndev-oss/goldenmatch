/**
 * Live catalog emit for the semantic-layer wedge (wedge B), from the store.
 *
 * `emitSemanticModelFromStore` reads the *live* control plane: the durable
 * IdentityStore already holds the `entity_id` ↔ `{source, source_pk}` mapping, so
 * the conformed `resolved_entity_id` join declaration can be regenerated at any
 * time — "keep the semantic layer's identity join live against the control
 * plane." Faithful port of Python `semantic/catalog.py::emit_semantic_model_from_store`.
 *
 * Edge-safety: this core returns the emitted YAML string only. The Python
 * function also writes it to a catalog file when `path` is set; that filesystem
 * write is a node concern and lives in the node MCP handler (`src/node/**`), so
 * `src/core/**` stays free of `node:*` — the same split the rest of the port
 * follows.
 */

import type { IdentityStore } from "../identity/types.js";
import { identitySummaryStats } from "../identity/profile.js";
import { ResolvedCrosswalk } from "./crosswalk.js";
import { emitFromCrosswalk } from "./metricflow.js";
import { emitCubeFromCrosswalk } from "./cube.js";
import { emitOsiFromCrosswalk } from "./osi.js";

export type SemanticDialect = "metricflow" | "cube" | "osi";
const DIALECTS: readonly SemanticDialect[] = ["metricflow", "cube", "osi"];

/** Emit kwargs forwarded to the dialect emitter (measures / grain live here). */
export interface EmitDialectExtras {
  measures?: readonly string[];
  grain?: readonly string[] | string | null;
  modelRef?: string;
}

/**
 * Emit a crosswalk's conformed entity declaration in the given dialect. Shared
 * by the store-emit surface; mirrors Python `_emit_for_dialect`.
 */
function emitForDialect(
  crosswalk: ResolvedCrosswalk,
  dialect: string,
  sourceTarget: string,
  extras: EmitDialectExtras = {},
): string {
  const key = dialect.trim().toLowerCase();
  if (!(DIALECTS as readonly string[]).includes(key)) {
    throw new Error(`unknown dialect ${JSON.stringify(dialect)}; expected one of ${DIALECTS.join(", ")}`);
  }
  if (key === "metricflow") {
    return emitFromCrosswalk(crosswalk, sourceTarget, {
      ...(extras.measures !== undefined ? { measures: extras.measures } : {}),
      ...(extras.grain !== undefined ? { grain: extras.grain } : {}),
      ...(extras.modelRef !== undefined ? { modelRef: extras.modelRef } : {}),
    });
  }
  if (key === "cube") {
    return emitCubeFromCrosswalk(crosswalk, { sourceCube: sourceTarget });
  }
  return emitOsiFromCrosswalk(crosswalk, { sourceDataset: sourceTarget });
}

export interface EmitSemanticModelFromStoreOptions {
  sourceName: string;
  sourcePkColumn: string;
  dialect?: SemanticDialect;
  dataset?: string | null;
  sourceTarget?: string;
  resolvedKey?: string;
  extras?: EmitDialectExtras;
}

/**
 * Emit the conformed entity declaration for a semantic layer directly from the
 * durable IdentityStore. The emitted declaration is identical in shape to the
 * crosswalk emitters' (they read provenance stats — record/entity counts, the
 * resolved key — not the row data), populated from the store's identity summary
 * for `dataset`. Returns the emitted catalog YAML.
 */
export async function emitSemanticModelFromStore(
  store: IdentityStore,
  opts: EmitSemanticModelFromStoreOptions,
): Promise<string> {
  const resolvedKey = opts.resolvedKey ?? "resolved_entity_id";
  const dataset = opts.dataset ?? null;
  const summary = await identitySummaryStats(store, dataset);
  const totalRecords = Number(summary["total_records"] ?? 0);
  const totalEntities = Number(summary["total_entities"] ?? 0);

  // The emitters read only provenance stats off the crosswalk, never a table, so
  // a stats-only stand-in reproduces the crosswalk emit exactly.
  const xw = new ResolvedCrosswalk({
    source: opts.sourceName,
    sourcePkColumn: opts.sourcePkColumn,
    resolvedKey,
    nRecords: totalRecords,
    nEntities: totalEntities,
    storePath: (store as { path?: string }).path ?? null,
  });

  const target = opts.sourceTarget || opts.sourceName;
  return emitForDialect(xw, opts.dialect ?? "metricflow", target, opts.extras ?? {});
}
