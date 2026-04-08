// InMemoryProvider — infer schema from an array of plain records.
// Mirrors infermap/providers/memory.py but narrowed to the edge-safe JS case:
// we don't carry a Polars/Pandas equivalent, just JSON-shaped data.
import type { SchemaInfo, FieldInfo } from "../types.js";
import { makeSchemaInfo } from "../types.js";
import { profileFieldFromValues } from "../util/profile.js";

export interface InMemoryOptions {
  sourceName?: string;
  sampleSize?: number;
  /**
   * Optional column order. If omitted, keys are collected from the union of
   * the first 100 records in order of first appearance. Providing a fixed
   * order is recommended for deterministic field ordering across runs.
   */
  columns?: string[];
}

export function inferSchemaFromRecords(
  records: readonly Record<string, unknown>[],
  options: InMemoryOptions = {}
): SchemaInfo {
  const sourceName = options.sourceName ?? "memory";
  const sampleSize = options.sampleSize ?? 500;

  let columns = options.columns;
  if (!columns) {
    const seen = new Set<string>();
    const ordered: string[] = [];
    const scanLimit = Math.min(records.length, 100);
    for (let i = 0; i < scanLimit; i++) {
      for (const key of Object.keys(records[i]!)) {
        if (!seen.has(key)) {
          seen.add(key);
          ordered.push(key);
        }
      }
    }
    columns = ordered;
  }

  const fields: FieldInfo[] = columns.map((col) => {
    const values = records.map((r) => r[col]);
    return profileFieldFromValues(col, values, sampleSize);
  });

  return makeSchemaInfo({ fields, sourceName });
}

export class InMemoryProvider {
  extract(
    records: readonly Record<string, unknown>[],
    options: InMemoryOptions = {}
  ): SchemaInfo {
    return inferSchemaFromRecords(records, options);
  }
}
