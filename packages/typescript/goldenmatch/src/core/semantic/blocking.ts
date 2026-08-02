/**
 * Metric-aware attribute selection for the semantic-layer resolution tier
 * (edge-safe port of Python `semantic/blocking.py`).
 *
 * A semantic model already declares which columns are entity **keys**, which are
 * **measures** (numeric aggregation targets — NOT identity evidence), and which
 * are **dimensions** (the identity-bearing attributes a metric groups by). The
 * resolution tier (`resolveKeyIntegrity`) runs entity resolution to detect key
 * fragmentation; feeding that ER the model's own measure/dimension metadata —
 * instead of blindly profiling every column — is the differentiated wedge. A
 * measure like `revenue` must never be a match signal, and a declared dimension
 * like `email` is exactly the attribute to resolve on. No pure-ER tool has this
 * metadata; the semantic model does.
 *
 * `semanticFieldRoles(doc)` reads the declared roles from any of the three
 * dialects (dbt/MetricFlow, Cube, OSI/Ossie). `metricAwareAttributes(roles,
 * columns)` turns them into the ER attribute allow-list: the declared dimensions
 * present in the frame (measures and keys always excluded), with a safe fallback
 * to "every non-key, non-measure column" when a model declares no dimensions — so
 * a model that declares dimensions gets the metric-aware selection and one that
 * doesn't is byte-identical to the blind selection the resolution tier used
 * before.
 */

import { type LoadedDoc, asList, isObj } from "./parseUtil.js";
import { detectDialect } from "./certify.js";
import { entityColumn, measureColumn } from "./metricflow.js";
import { parseCubeModels } from "./cube.js";
import { parseOsiModels } from "./osi.js";
import type { SemanticFrame } from "./frame.js";

/** The column roles a semantic model declares, unioned across all of its
 * models / cubes / datasets.
 *
 * - `keys` — declared entity key columns (primary / natural / unique).
 * - `dimensions` — declared dimension columns (identity-bearing attributes).
 * - `measures` — declared measure columns (aggregation targets, never identity).
 */
export interface SemanticFieldRoles {
  keys: string[];
  dimensions: string[];
  measures: string[];
}

/** Order-preserving de-duplication (a column may be declared more than once). */
function dedup(seq: readonly string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const c of seq) {
    if (c && !seen.has(c)) {
      seen.add(c);
      out.push(c);
    }
  }
  return out;
}

/** Column names of a TS semantic frame (a `{ column: values[] }` map). Mirrors
 * Python `_frame_columns` for the one table shape the edge-safe port carries. */
export function frameColumns(df: SemanticFrame): string[] {
  return Object.keys(df);
}

/**
 * Read the declared `{keys, dimensions, measures}` roles from a semantic model.
 * Mirrors Python `semantic_field_roles`. The dialect is auto-detected from the
 * loaded document; roles are unioned across every model / cube / dataset.
 */
export function semanticFieldRoles(doc: LoadedDoc): SemanticFieldRoles {
  const dialect = detectDialect(doc);
  const keys: string[] = [];
  const dimensions: string[] = [];
  const measures: string[] = [];

  if (dialect === "metricflow") {
    for (const sm of asList(doc["semantic_models"])) {
      if (!isObj(sm)) continue;
      for (const ent of asList(sm["entities"])) {
        if (isObj(ent)) {
          const col = entityColumn(ent);
          if (col) keys.push(col);
        }
      }
      for (const dim of asList(sm["dimensions"])) {
        // dimensions share the entity `expr`-or-`name` column shape
        if (isObj(dim)) {
          const col = entityColumn(dim);
          if (col) dimensions.push(col);
        }
      }
      for (const m of asList(sm["measures"])) {
        if (isObj(m)) {
          const col = measureColumn(m);
          if (col) measures.push(col);
        }
      }
    }
  } else if (dialect === "cube") {
    for (const cube of parseCubeModels(doc)) {
      for (const d of cube.dimensions) {
        (d.primaryKey ? keys : dimensions).push(d.name);
      }
      for (const m of cube.measures) measures.push(m.name);
    }
  } else {
    // osi
    for (const model of parseOsiModels(doc)) {
      for (const ds of model.datasets) {
        keys.push(...ds.primaryKey);
        const keySet = new Set(ds.primaryKey);
        for (const f of ds.fields) {
          if (!keySet.has(f.name)) dimensions.push(f.name);
        }
      }
      // OSI metrics are aggregation expressions (often derived names, not raw
      // frame columns); record them so an incidental same-named column is still
      // excluded from identity evidence.
      for (const metric of model.metrics) {
        if (metric.name) measures.push(metric.name);
      }
    }
  }

  return { keys: dedup(keys), dimensions: dedup(dimensions), measures: dedup(measures) };
}

/**
 * The entity-resolution attribute allow-list for a frame, given declared roles.
 * Mirrors Python `metric_aware_attributes`.
 *
 * Measures and keys are always excluded (a measure is an aggregation target, a
 * key is what resolution is checking — neither is identity evidence). When the
 * model declares dimensions, the result is exactly the declared dimensions
 * present in the frame; otherwise it falls back to every remaining column, so a
 * model that declares no dimensions is byte-identical to the blind selection. The
 * result preserves frame-column order for determinism.
 */
export function metricAwareAttributes(
  roles: SemanticFieldRoles,
  columns: readonly string[],
): string[] {
  const cols = [...columns];
  const excluded = new Set<string>([...roles.keys, ...roles.measures]);
  const colSet = new Set(cols);
  const declaredDims = roles.dimensions.filter((c) => colSet.has(c) && !excluded.has(c));
  if (declaredDims.length) {
    const keep = new Set(declaredDims);
    return cols.filter((c) => keep.has(c));
  }
  // No declared dimensions present → blind fallback (measures/keys still excluded).
  return cols.filter((c) => !excluded.has(c));
}
