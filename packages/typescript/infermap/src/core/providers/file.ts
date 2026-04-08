// FileProvider (edge-safe) — infer schema from CSV or JSON *text*.
// Node-side wrappers that read from disk live under infermap/node.
import type { SchemaInfo } from "../types.js";
import { parseCsv } from "../util/csv.js";
import { inferSchemaFromRecords } from "./in-memory.js";

export interface FileProviderOptions {
  sourceName?: string;
  sampleSize?: number;
}

export function inferSchemaFromCsvText(
  text: string,
  options: FileProviderOptions = {}
): SchemaInfo {
  const { headers, rows } = parseCsv(text);
  const inner: Parameters<typeof inferSchemaFromRecords>[1] = {
    sourceName: options.sourceName ?? "csv",
    columns: headers,
  };
  if (options.sampleSize !== undefined) inner.sampleSize = options.sampleSize;
  return inferSchemaFromRecords(rows, inner);
}

export function inferSchemaFromJsonText(
  text: string,
  options: FileProviderOptions = {}
): SchemaInfo {
  const parsed = JSON.parse(text);
  if (!Array.isArray(parsed)) {
    throw new TypeError(
      "inferSchemaFromJsonText: JSON root must be an array of records"
    );
  }
  const inner: Parameters<typeof inferSchemaFromRecords>[1] = {
    sourceName: options.sourceName ?? "json",
  };
  if (options.sampleSize !== undefined) inner.sampleSize = options.sampleSize;
  return inferSchemaFromRecords(parsed as Record<string, unknown>[], inner);
}
