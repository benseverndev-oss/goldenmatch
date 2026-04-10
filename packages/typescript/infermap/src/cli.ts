#!/usr/bin/env node
// infermap CLI — map, apply, inspect, validate.
// Mirrors infermap/cli.py but with TS ergonomics. Uses node:util/parseArgs
// (stable since Node 18) to stay zero-runtime-dep.
//
// Inputs accepted for <source>/<target>:
//   - .csv file path
//   - .json file path (array of records OR { "fields": [...] } schema definition)
//   - database URI (sqlite://, postgresql://, postgres://, duckdb://) + --table

import { parseArgs } from "node:util";
import { readFile, writeFile } from "node:fs/promises";
import { extname } from "node:path";

import { MapEngine } from "./core/engine.js";
import { fromConfig, mapResultToConfigJson, ConfigError } from "./core/config.js";
import { mapResultToJson, mapResultToReport } from "./core/types.js";
import type { MapResult, SchemaInfo } from "./core/types.js";
import { extractSchemaFromFile } from "./node/fs.js";
import { extractDbSchema } from "./node/db/provider.js";
import { parseCsv } from "./core/util/csv.js";

const USAGE = `infermap — inference-driven schema mapping engine

Usage:
  infermap map <source> <target> [options]
  infermap apply <source> --config <file> --output <file>
  infermap inspect <source> [--table <name>]
  infermap validate <source> --config <file> [--required <fields>] [--strict]
  infermap --help

Common options:
  --table <name>         table name for DB sources
  --format <fmt>         output format: table | json  (default: table)
  --required <fields>    comma-separated required target field names
  --schema-file <path>   JSON schema definition file merged into target
  --min-confidence <n>   minimum confidence threshold (default 0.2)
  -o, --output <path>    write config JSON to this path
  --config <path>        input config (for apply/validate)
  --strict               exit 1 if required fields unmapped (validate)
`;

function die(message: string, code = 1): never {
  process.stderr.write(`Error: ${message}\n`);
  process.exit(code);
}

function isDbUri(source: string): boolean {
  return /^(sqlite|postgresql|postgres|duckdb|mysql):\/\//i.test(source);
}

async function resolveSchema(
  source: string,
  table: string | undefined
): Promise<SchemaInfo> {
  if (isDbUri(source)) {
    if (!table) die(`--table is required when source is a database URI (${source})`);
    return extractDbSchema(source, { table: table! });
  }
  return extractSchemaFromFile(source);
}

function printTable(result: MapResult): void {
  const header = `${"SOURCE".padEnd(30)} ${"TARGET".padEnd(30)} ${"CONF".padStart(6)}  REASONING`;
  process.stdout.write(`${header}\n${"-".repeat(90)}\n`);
  for (const m of result.mappings) {
    const reasoning =
      m.reasoning.length > 40 ? `${m.reasoning.slice(0, 40)}...` : m.reasoning;
    const conf = m.confidence.toFixed(3).padStart(6);
    process.stdout.write(
      `${m.source.padEnd(30)} ${m.target.padEnd(30)} ${conf}  ${reasoning}\n`
    );
  }
  if (result.unmappedSource.length > 0) {
    process.stdout.write(
      `\nUnmapped source fields: ${result.unmappedSource.join(", ")}\n`
    );
  }
  if (result.unmappedTarget.length > 0) {
    process.stdout.write(
      `Unmapped target fields: ${result.unmappedTarget.join(", ")}\n`
    );
  }
  if (result.warnings.length > 0) {
    process.stdout.write("\nWarnings:\n");
    for (const w of result.warnings) process.stdout.write(`  ! ${w}\n`);
  }
}

// ---------- commands ----------

async function cmdMap(argv: string[]): Promise<number> {
  const { values, positionals } = parseArgs({
    args: argv,
    allowPositionals: true,
    options: {
      table: { type: "string" },
      required: { type: "string" },
      "schema-file": { type: "string" },
      format: { type: "string", default: "table" },
      output: { type: "string", short: "o" },
      "min-confidence": { type: "string", default: "0.2" },
    },
  });
  const [source, target] = positionals;
  if (!source || !target) {
    die("map requires <source> and <target> positional arguments");
  }

  const src = await resolveSchema(source, values.table);
  const tgt = await resolveSchema(target, values.table);

  const required =
    typeof values.required === "string"
      ? values.required.split(",").map((s) => s.trim()).filter(Boolean)
      : undefined;

  let schemaFile: SchemaInfo | undefined;
  if (typeof values["schema-file"] === "string") {
    schemaFile = await extractSchemaFromFile(values["schema-file"]);
  }

  const engine = new MapEngine({
    minConfidence: Number(values["min-confidence"]),
  });
  const subOpts: Parameters<typeof engine.mapSchemas>[2] = {};
  if (required !== undefined) subOpts.required = required;
  if (schemaFile !== undefined) subOpts.schemaFile = schemaFile;
  const result = engine.mapSchemas(src, tgt, subOpts);

  if (values.format === "json") {
    process.stdout.write(`${mapResultToJson(result)}\n`);
  } else {
    printTable(result);
  }

  if (typeof values.output === "string") {
    await writeFile(values.output, mapResultToConfigJson(result), "utf8");
  }
  return 0;
}

async function cmdApply(argv: string[]): Promise<number> {
  const { values, positionals } = parseArgs({
    args: argv,
    allowPositionals: true,
    options: {
      config: { type: "string" },
      output: { type: "string", short: "o" },
    },
  });
  const [source] = positionals;
  if (!source) die("apply requires <source> positional argument");
  if (!values.config) die("apply requires --config <path>");
  if (!values.output) die("apply requires --output <path>");

  if (extname(source).toLowerCase() !== ".csv") {
    die("apply currently only supports CSV sources");
  }

  let mapResult: MapResult;
  try {
    const cfgText = await readFile(values.config, "utf8");
    mapResult = fromConfig(cfgText);
  } catch (e) {
    if (e instanceof ConfigError) die(`Config error: ${e.message}`);
    die(`Could not read config: ${String(e)}`);
  }

  const text = await readFile(source, "utf8");
  const { headers, rows } = parseCsv(text);

  // Build rename map, failing on missing source columns
  const renameMap = new Map<string, string>();
  const colSet = new Set(headers);
  for (const m of mapResult.mappings) {
    if (!colSet.has(m.source)) {
      die(`Source column missing from CSV: ${m.source}`);
    }
    renameMap.set(m.source, m.target);
  }
  const newHeaders = headers.map((h) => renameMap.get(h) ?? h);

  // Write output CSV
  const escape = (v: string): string =>
    /[",\r\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
  const lines = [newHeaders.map(escape).join(",")];
  for (const row of rows) {
    lines.push(headers.map((h) => escape(row[h] ?? "")).join(","));
  }
  await writeFile(values.output, `${lines.join("\n")}\n`, "utf8");
  return 0;
}

async function cmdInspect(argv: string[]): Promise<number> {
  const { values, positionals } = parseArgs({
    args: argv,
    allowPositionals: true,
    options: { table: { type: "string" } },
  });
  const [source] = positionals;
  if (!source) die("inspect requires <source> positional argument");

  const schema = await resolveSchema(source, values.table);
  process.stdout.write(`Source: ${schema.sourceName || source}\n`);
  process.stdout.write(`Fields: ${schema.fields.length}\n\n`);
  const header = `${"FIELD".padEnd(30)} ${"TYPE".padEnd(12)} ${"NULL%".padStart(6)}  ${"UNIQ%".padStart(6)}  SAMPLES`;
  process.stdout.write(`${header}\n${"-".repeat(85)}\n`);
  for (const f of schema.fields) {
    const samples = f.sampleValues.slice(0, 3).join(", ");
    const nullPct = `${(f.nullRate * 100).toFixed(1)}%`.padStart(6);
    const uniqPct = `${(f.uniqueRate * 100).toFixed(1)}%`.padStart(6);
    process.stdout.write(
      `${f.name.padEnd(30)} ${f.dtype.padEnd(12)} ${nullPct}  ${uniqPct}  ${samples}\n`
    );
  }
  return 0;
}

async function cmdValidate(argv: string[]): Promise<number> {
  const { values, positionals } = parseArgs({
    args: argv,
    allowPositionals: true,
    options: {
      config: { type: "string" },
      required: { type: "string" },
      strict: { type: "boolean", default: false },
    },
  });
  const [source] = positionals;
  if (!source) die("validate requires <source> positional argument");
  if (!values.config) die("validate requires --config <path>");

  let mapResult: MapResult;
  try {
    const cfgText = await readFile(values.config, "utf8");
    mapResult = fromConfig(cfgText);
  } catch (e) {
    if (e instanceof ConfigError) die(`Config error: ${e.message}`);
    die(`Could not read config: ${String(e)}`);
  }

  const schema = await resolveSchema(source, undefined);
  const sourceCols = new Set(schema.fields.map((f) => f.name));

  const missingSources = mapResult.mappings
    .filter((m) => !sourceCols.has(m.source))
    .map((m) => m.source);
  if (missingSources.length > 0) {
    process.stdout.write(`Missing source columns: ${missingSources.join(", ")}\n`);
  } else {
    process.stdout.write("All mapped source columns are present.\n");
  }

  const requiredList =
    typeof values.required === "string"
      ? values.required.split(",").map((s) => s.trim()).filter(Boolean)
      : [];
  const mappedTargets = new Set(mapResult.mappings.map((m) => m.target));
  const missingRequired = requiredList.filter((r) => !mappedTargets.has(r));

  if (missingRequired.length > 0) {
    process.stdout.write(`Required fields not mapped: ${missingRequired.join(", ")}\n`);
    if (values.strict) return 1;
  } else if (requiredList.length > 0) {
    process.stdout.write("All required fields are mapped.\n");
  }
  return 0;
}

// ---------- entrypoint ----------

async function main(): Promise<number> {
  const [, , cmd, ...rest] = process.argv;

  if (!cmd || cmd === "--help" || cmd === "-h") {
    process.stdout.write(USAGE);
    return 0;
  }

  // Touch mapResultToReport so the exported helper gets bundled.
  void mapResultToReport;

  try {
    switch (cmd) {
      case "map":
        return await cmdMap(rest);
      case "apply":
        return await cmdApply(rest);
      case "inspect":
        return await cmdInspect(rest);
      case "validate":
        return await cmdValidate(rest);
      default:
        process.stderr.write(`Unknown command: ${cmd}\n\n${USAGE}`);
        return 1;
    }
  } catch (e) {
    process.stderr.write(`Error: ${e instanceof Error ? e.message : String(e)}\n`);
    return 1;
  }
}

main().then((code) => process.exit(code));
