// Node-only filesystem wrappers. Read CSV / JSON / schema-definition files
// from disk and hand off to the edge-safe text-based providers in core.
import { readFile } from "node:fs/promises";
import { basename, extname } from "node:path";
import {
  inferSchemaFromCsvText,
  inferSchemaFromJsonText,
  type FileProviderOptions,
} from "../core/providers/file.js";
import { parseSchemaDefinition } from "../core/providers/schema-file.js";
import type { SchemaInfo } from "../core/types.js";

function stem(path: string): string {
  const b = basename(path);
  const ext = extname(b);
  return ext ? b.slice(0, -ext.length) : b;
}

export async function extractSchemaFromFile(
  path: string,
  options: FileProviderOptions = {}
): Promise<SchemaInfo> {
  const text = await readFile(path, "utf8");
  const ext = extname(path).toLowerCase();
  const sourceName = options.sourceName ?? stem(path);

  if (ext === ".csv") {
    const opts: FileProviderOptions = { sourceName };
    if (options.sampleSize !== undefined) opts.sampleSize = options.sampleSize;
    return inferSchemaFromCsvText(text, opts);
  }
  if (ext === ".json") {
    // Disambiguate: JSON schema definition (has "fields" key) vs. array of records.
    try {
      const parsed = JSON.parse(text);
      if (Array.isArray(parsed)) {
        const opts: FileProviderOptions = { sourceName };
        if (options.sampleSize !== undefined) opts.sampleSize = options.sampleSize;
        return inferSchemaFromJsonText(text, opts);
      }
      if (parsed && typeof parsed === "object" && "fields" in parsed) {
        return parseSchemaDefinition(text, sourceName);
      }
      throw new TypeError(
        "JSON file must be either an array of records or a schema definition with a 'fields' key"
      );
    } catch (e) {
      if (e instanceof SyntaxError) {
        throw new SyntaxError(`Invalid JSON in ${path}: ${e.message}`);
      }
      throw e;
    }
  }

  throw new Error(`Unsupported file extension for ${path}: ${ext || "(none)"}`);
}
