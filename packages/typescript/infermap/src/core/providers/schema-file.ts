// SchemaFileProvider — parses a JSON schema definition file.
// YAML is not supported in the TS port (JSON-only config decision).
//
// Expected shape (matches the Python YAML/JSON schema_file format):
// {
//   "fields": [
//     { "name": "customer_id", "dtype": "string", "aliases": ["cust_id"], "required": true },
//     ...
//   ]
// }
import type { FieldInfo, SchemaInfo } from "../types.js";
import { makeFieldInfo, makeSchemaInfo } from "../types.js";

interface SchemaFieldEntry {
  name: string;
  dtype?: string;
  aliases?: string[];
  required?: boolean;
}

interface SchemaDefinition {
  fields: SchemaFieldEntry[];
}

export class SchemaParseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SchemaParseError";
  }
}

export function parseSchemaDefinition(
  json: string | SchemaDefinition,
  sourceName = "schema"
): SchemaInfo {
  const raw: unknown = typeof json === "string" ? JSON.parse(json) : json;
  if (!raw || typeof raw !== "object" || !("fields" in raw)) {
    throw new SchemaParseError(
      "Schema file must contain a top-level 'fields' key"
    );
  }
  const rawFields = (raw as { fields: unknown }).fields;
  if (!Array.isArray(rawFields)) {
    throw new SchemaParseError("Schema file 'fields' must be an array");
  }

  const fields: FieldInfo[] = [];
  const requiredFields: string[] = [];

  for (const entry of rawFields as SchemaFieldEntry[]) {
    if (!entry || typeof entry.name !== "string") {
      throw new SchemaParseError(
        "Each field entry must be an object with a 'name' string"
      );
    }
    const aliases = Array.isArray(entry.aliases)
      ? entry.aliases.filter((a): a is string => typeof a === "string")
      : [];
    const metadata: Record<string, unknown> = {};
    if (aliases.length > 0) metadata["aliases"] = aliases;

    fields.push(
      makeFieldInfo({
        name: entry.name,
        dtype: entry.dtype ?? "string",
        sampleValues: [],
        nullRate: 0,
        uniqueRate: 0,
        valueCount: 0,
        metadata,
      })
    );

    if (entry.required) requiredFields.push(entry.name);
  }

  return makeSchemaInfo({ fields, sourceName, requiredFields });
}
