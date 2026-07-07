/**
 * TransformEngine — the main orchestrator.
 * Dispatches transforms by mode (expr/series/dataframe) on Row[] data.
 */

import type {
  ColumnValue,
  GoldenFlowConfig,
  Row,
  TransformInfo,
  TransformResult,
} from "../types.js";
import { makeConfig, makeTransformRecord, MutableManifest } from "../types.js";
import { toColumnValue } from "../data.js";
import { getTransform, parseTransformName } from "../transforms/index.js";
import { fusedEnabled, isFusable } from "./_chain.js";
import { profileDataframe } from "./profiler-bridge.js";
import { selectTransforms } from "./selector.js";
import { getFlowWasmBackend } from "../wasm/backend.js";

export class TransformEngine {
  readonly config: GoldenFlowConfig;

  constructor(config?: Partial<GoldenFlowConfig>) {
    this.config = makeConfig(config);
  }

  transformDf(
    rows: readonly Row[],
    source = "<dataframe>",
  ): TransformResult {
    const manifest = new MutableManifest(source);
    let currentRows = [...rows] as Row[];

    if (this.config.transforms.length > 0) {
      currentRows = this._applyConfigTransforms(currentRows, manifest);
    } else {
      currentRows = this._applyAutoTransforms(currentRows, manifest, source);
    }

    // Apply splits
    for (const split of this.config.splits) {
      if (currentRows.length === 0 || !(split.source in currentRows[0]!)) continue;
      const info = getTransform(split.method);
      if (info && info.mode === "dataframe") {
        currentRows = info.func(currentRows, split.source);
      }
    }

    // Apply renames
    for (const [oldName, newName] of Object.entries(this.config.renames)) {
      if (currentRows.length === 0 || !(oldName in currentRows[0]!)) continue;
      currentRows = currentRows.map((row) => {
        const newRow: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(row)) {
          newRow[k === oldName ? newName : k] = v;
        }
        return newRow;
      });
    }

    // Apply drops
    const dropCols = new Set(this.config.drop);
    if (dropCols.size > 0 && currentRows.length > 0) {
      const existingDrops = [...dropCols].filter((c) => c in currentRows[0]!);
      if (existingDrops.length > 0) {
        const dropSet = new Set(existingDrops);
        currentRows = currentRows.map((row) => {
          const newRow: Record<string, unknown> = {};
          for (const [k, v] of Object.entries(row)) {
            if (!dropSet.has(k)) newRow[k] = v;
          }
          return newRow;
        });
      }
    }

    // Apply filters
    for (const filt of this.config.filters) {
      if (currentRows.length === 0 || !(filt.column in currentRows[0]!)) continue;
      currentRows = this._applyFilter(currentRows, filt.column, filt.condition);
    }

    // Apply dedup
    if (this.config.dedup) {
      const dedupCols = this.config.dedup.columns.filter(
        (c) => currentRows.length > 0 && c in currentRows[0]!,
      );
      if (dedupCols.length > 0) {
        const before = currentRows.length;
        const seen = new Set<string>();
        const deduped: Row[] = [];
        const iterRows =
          this.config.dedup.keep === "last" ? [...currentRows].reverse() : currentRows;
        for (const row of iterRows) {
          const key = dedupCols.map((c) => String(row[c] ?? "")).join("\0");
          if (!seen.has(key)) {
            seen.add(key);
            deduped.push(row);
          }
        }
        if (this.config.dedup.keep === "last") deduped.reverse();
        currentRows = deduped;
        const after = currentRows.length;
        if (before !== after) {
          manifest.addRecord(
            makeTransformRecord({
              column: dedupCols.join(","),
              transform: "dedup",
              affectedRows: before - after,
              totalRows: before,
            }),
          );
        }
      }
    }

    const columns =
      currentRows.length > 0 ? Object.keys(currentRows[0]!) : [];

    return { rows: currentRows, columns, manifest };
  }

  private _applyConfigTransforms(
    rows: Row[],
    manifest: MutableManifest,
  ): Row[] {
    for (const spec of this.config.transforms) {
      if (rows.length === 0 || !(spec.column in rows[0]!)) continue;
      const ops: Array<[TransformInfo, string[]]> = [];
      for (const opRaw of spec.ops) {
        const [name, params] = parseTransformName(opRaw);
        const info = getTransform(name);
        if (!info) {
          manifest.addError(
            spec.column,
            name,
            -1,
            `Transform '${name}' not found in registry`,
          );
          continue;
        }
        ops.push([info, params]);
      }
      rows = this._applyColumnOps(rows, spec.column, ops, manifest);
    }
    return rows;
  }

  private _applyAutoTransforms(
    rows: Row[],
    manifest: MutableManifest,
    source: string,
  ): Row[] {
    const filePath = source !== "<dataframe>" ? source : "";
    const profile = profileDataframe(rows, filePath);

    for (const colProfile of profile.columns) {
      const selected = selectTransforms(colProfile);
      const ops: Array<[TransformInfo, string[]]> = selected.map((info) => [
        info,
        [],
      ]);
      rows = this._applyColumnOps(rows, colProfile.name, ops, manifest);
    }
    return rows;
  }

  /**
   * Apply an ordered list of `[info, params]` to `column`. When the WASM backend
   * is active, a maximal run of owned no-arg string kernels is fused into ONE
   * `applyChain` crossing (Pillar-1 on the edge); everything else takes the
   * per-transform path unchanged. Pure-TS (no backend) always takes per-transform.
   */
  private _applyColumnOps(
    rows: Row[],
    column: string,
    ops: Array<[TransformInfo, string[]]>,
    manifest: MutableManifest,
  ): Row[] {
    const fuseOn = fusedEnabled();
    let i = 0;
    while (i < ops.length) {
      const [info, params] = ops[i]!;
      if (fuseOn && isFusable(info.name, params)) {
        let j = i;
        while (j < ops.length && isFusable(ops[j]![0].name, ops[j]![1])) j++;
        if (j - i >= 2) {
          const applied = this._applyFusedRun(
            rows,
            column,
            ops.slice(i, j),
            manifest,
          );
          if (applied !== null) {
            rows = applied;
            i = j;
            continue;
          }
          // backend declined -> fall through to the per-op path.
        }
      }
      rows = this._applySingleTransform(rows, column, info, params, manifest);
      i++;
    }
    return rows;
  }

  /**
   * Apply a run of fusable ops via one `backend.applyChain` crossing. Emits one
   * audit record per op with the exact affected count from the kernel and per-step
   * before/after samples from a cheap head(3) replay through the SAME transforms
   * (byte-identical to the fused output). Returns the new rows, or `null` if the
   * backend declined (caller falls back to per-op).
   */
  private _applyFusedRun(
    rows: Row[],
    column: string,
    run: Array<[TransformInfo, string[]]>,
    manifest: MutableManifest,
  ): Row[] | null {
    const backend = getFlowWasmBackend();
    if (backend == null || typeof backend.applyChain !== "function") return null;

    // Non-null values (coerced to string) + their row indices. Total kernels never
    // null, so nulls stay put and only the non-null cells go across the boundary.
    const idx: number[] = [];
    const vals: string[] = [];
    for (let r = 0; r < rows.length; r++) {
      const v = rows[r]![column];
      if (v === null || v === undefined) continue;
      idx.push(r);
      vals.push(typeof v === "string" ? v : String(v));
    }
    const names = run.map(([info]) => info.name);
    let outVals: string[];
    let changed: number[];
    try {
      const out = backend.applyChain(vals, names);
      outVals = out.values;
      changed = out.changed;
    } catch {
      return null; // fall back to per-op
    }
    if (outVals.length !== vals.length || changed.length !== run.length) {
      return null;
    }

    // Manifest: one record per op. Affected count is exact from the kernel; the
    // before/after samples come from replaying each op on the first-3 rows through
    // the SAME transform funcs (which dispatch to the same backend kernels).
    const totalRows = rows.length;
    let sample: ColumnValue[] = rows.slice(0, 3).map((r) => {
      const v = r[column];
      if (v === null || v === undefined) return null;
      return typeof v === "string" || typeof v === "number" || typeof v === "boolean"
        ? v
        : String(v);
    });
    for (let k = 0; k < run.length; k++) {
      const [info, params] = run[k]!;
      const before = sample.map((v) => String(v ?? ""));
      const typedParams = castParams(params);
      const res =
        typedParams.length > 0 ? info.func(sample, ...typedParams) : info.func(sample);
      sample = res as ColumnValue[];
      const after = sample.map((v) => String(v ?? ""));
      manifest.addRecord(
        makeTransformRecord({
          column,
          transform: info.name,
          affectedRows: changed[k] ?? 0,
          totalRows,
          sampleBefore: before,
          sampleAfter: after,
        }),
      );
    }

    // Scatter the transformed values back into their (non-null) positions.
    const newRows = rows.slice();
    for (let m = 0; m < idx.length; m++) {
      const r = idx[m]!;
      const nv = outVals[m]!;
      if (String(newRows[r]![column] ?? "") !== nv) {
        newRows[r] = { ...newRows[r]!, [column]: nv };
      }
    }
    return newRows;
  }

  private _applySingleTransform(
    rows: Row[],
    column: string,
    info: TransformInfo,
    params: string[],
    manifest: MutableManifest,
  ): Row[] {
    const totalRows = rows.length;
    const beforeSample = rows
      .slice(0, 3)
      .map((r) => String(r[column] ?? ""));

    try {
      let newRows: Row[];

      if (info.mode === "dataframe") {
        newRows = info.func(rows, column, ...castParams(params));
      } else {
        // expr and series both operate on column values array
        // Pass raw values to transforms — don't pre-convert null strings,
        // transforms like null_standardize need to see the original "N/A" etc.
        const values = rows.map((r) => {
          const v = r[column];
          if (v === null || v === undefined) return null;
          if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return v;
          return String(v);
        }) as ColumnValue[];
        const typedParams = castParams(params);
        const result = typedParams.length > 0
          ? info.func(values, ...typedParams)
          : info.func(values);

        let newValues: ColumnValue[];
        if (Array.isArray(result) && result.length === 2 && Array.isArray(result[1])) {
          // Tuple return: [values, flaggedRows] (e.g. initial_expand)
          newValues = result[0] as ColumnValue[];
          const flagged = result[1] as number[];
          for (const rowIdx of flagged) {
            manifest.addError(column, info.name, rowIdx, "Flagged for review");
          }
        } else {
          newValues = result as ColumnValue[];
        }

        newRows = rows.map((row, i) => {
          const oldVal = row[column] ?? null;
          if (newValues[i] === oldVal) return row;
          return { ...row, [column]: newValues[i] };
        });
      }

      const afterSample = newRows
        .slice(0, 3)
        .map((r) => String(r[column] ?? ""));

      // Count affected rows
      let changed = 0;
      for (let i = 0; i < Math.min(rows.length, newRows.length); i++) {
        if (String(rows[i]![column] ?? "") !== String(newRows[i]![column] ?? "")) {
          changed++;
        }
      }

      manifest.addRecord(
        makeTransformRecord({
          column,
          transform: info.name,
          affectedRows: changed,
          totalRows,
          sampleBefore: beforeSample,
          sampleAfter: afterSample,
        }),
      );

      return newRows;
    } catch (e) {
      manifest.addError(
        column,
        info.name,
        -1,
        e instanceof Error ? e.message : String(e),
      );
      return rows; // preserve original on failure
    }
  }

  private _applyFilter(rows: Row[], column: string, condition: string): Row[] {
    if (condition === "not_null") {
      return rows.filter((r) => r[column] !== null && r[column] !== undefined);
    }
    if (condition.startsWith("after:")) {
      const dateStr = condition.slice(6);
      return rows.filter((r) => String(r[column] ?? "") > dateStr);
    }
    if (condition.startsWith("before:")) {
      const dateStr = condition.slice(7);
      return rows.filter((r) => String(r[column] ?? "") < dateStr);
    }
    return rows;
  }
}

function castParams(params: string[]): unknown[] {
  return params.map((p) => {
    const asInt = parseInt(p, 10);
    if (!Number.isNaN(asInt) && String(asInt) === p) return asInt;
    const asFloat = parseFloat(p);
    if (!Number.isNaN(asFloat)) return asFloat;
    return p;
  });
}
