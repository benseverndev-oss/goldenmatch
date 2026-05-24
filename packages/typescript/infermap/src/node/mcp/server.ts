#!/usr/bin/env node
/**
 * mcp/server.ts -- InferMap MCP server (stdio transport, JSON-RPC 2.0).
 *
 * Node-only: uses node:fs, node:path, node:readline. NOT edge-safe.
 *
 * Exposes 4 tools (`map`, `inspect`, `validate`, `apply`) wired to the
 * existing InferMap TS core/node APIs (MapEngine, file/DB providers,
 * fromConfig). Also exposes resources (Supported Domains, Scorer Pipeline,
 * Last Mapping Report) and prompts (map-walkthrough, compare-schemas,
 * domain-mapping) -- byte-parity with the Python sibling at
 * `packages/python/infermap/infermap/mcp/server.py`.
 *
 * Every tool dispatch is wrapped in try/catch so a single failure never
 * crashes the JSON-RPC loop; errors come back as `{ error: "<msg>" }`.
 *
 * Mirrors the hand-rolled JSON-RPC pattern in
 * `packages/typescript/goldenmatch/src/node/mcp/server.ts` (no MCP SDK dep),
 * extended with resources/prompts methods to match the Python InferMap server.
 */

import { readFile, writeFile } from "node:fs/promises";
import { resolve, isAbsolute, sep, extname } from "node:path";
import { createInterface } from "node:readline";

import { MapEngine } from "../../core/engine.js";
import { fromConfig } from "../../core/config.js";
import { mapResultToReport } from "../../core/types.js";
import type { MapResultReport } from "../../core/types.js";
import type { SchemaInfo } from "../../core/types.js";
import { availableDomains } from "../../core/dictionaries/index.js";
import { defaultScorers } from "../../core/scorers/registry.js";
import { parseCsv } from "../../core/util/csv.js";
import { extractSchemaFromFile } from "../fs.js";
import { extractDbSchema } from "../db/provider.js";

// ---------------------------------------------------------------------------
// Tool definitions
// ---------------------------------------------------------------------------

interface Tool {
  readonly name: string;
  readonly description: string;
  readonly inputSchema: Readonly<Record<string, unknown>>;
}

export const TOOLS: readonly Tool[] = [
  {
    name: "map",
    description:
      "Map source columns to target schema using a weighted scorer pipeline " +
      "with optimal 1:1 assignment. Returns mappings with confidence scores " +
      "and human-readable reasoning.",
    inputSchema: {
      type: "object",
      properties: {
        source: {
          type: "string",
          description:
            "Path to source data (CSV, JSON, DB URI, schema definition)",
        },
        target: {
          type: "string",
          description: "Path to target data (same variety of inputs)",
        },
        table: {
          type: "string",
          description: "Table name for DB sources (optional)",
        },
        schema_file: {
          type: "string",
          description: "Path to schema definition file merged into target (optional)",
        },
        min_confidence: {
          type: "number",
          description: "Minimum confidence threshold (default 0.2)",
          default: 0.2,
        },
        domains: {
          type: "array",
          items: { type: "string" },
          description:
            "Domain dictionaries to load (e.g. ['healthcare', 'finance'])",
        },
      },
      required: ["source", "target"],
    },
  },
  {
    name: "inspect",
    description:
      "Inspect a data source — show fields, types, sample values, null rates, " +
      "and unique rates.",
    inputSchema: {
      type: "object",
      properties: {
        source: {
          type: "string",
          description: "Path to data source (CSV, JSON, DB URI, schema definition)",
        },
        table: {
          type: "string",
          description: "Table name for DB sources (optional)",
        },
      },
      required: ["source"],
    },
  },
  {
    name: "validate",
    description:
      "Validate that a source file's columns satisfy a saved mapping config. " +
      "Reports missing source columns and unmapped required fields.",
    inputSchema: {
      type: "object",
      properties: {
        source: { type: "string", description: "Path to source data file" },
        config: { type: "string", description: "Path to mapping config JSON file" },
        required_fields: {
          type: "array",
          items: { type: "string" },
          description: "Target field names that must be mapped",
        },
      },
      required: ["source", "config"],
    },
  },
  {
    name: "apply",
    description:
      "Apply a saved mapping config to a source CSV, renaming columns " +
      "according to the mapping and writing the result to an output file.",
    inputSchema: {
      type: "object",
      properties: {
        source: { type: "string", description: "Path to source CSV file" },
        config: { type: "string", description: "Path to mapping config JSON file" },
        output: { type: "string", description: "Output CSV file path" },
      },
      required: ["source", "config", "output"],
    },
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function sanitizePath(raw: unknown): string {
  if (typeof raw !== "string" || raw.length === 0) {
    throw new Error("path must be a non-empty string");
  }
  const resolved = isAbsolute(raw) ? resolve(raw) : resolve(process.cwd(), raw);
  const cwd = resolve(process.cwd());
  // Guard against prefix-bypass: cwd="/app/foo" must NOT accept "/app/foobar".
  if (resolved !== cwd && !resolved.startsWith(cwd + sep)) {
    throw new Error(`Path '${raw}' is outside the working directory`);
  }
  return resolved;
}

function isDbUri(source: string): boolean {
  return /^(sqlite|postgresql|postgres|duckdb|mysql):\/\//i.test(source);
}

async function resolveSchema(
  source: string,
  table: string | undefined,
): Promise<SchemaInfo> {
  if (isDbUri(source)) {
    if (!table) {
      throw new Error(`table is required when source is a database URI (${source})`);
    }
    return extractDbSchema(source, { table });
  }
  return extractSchemaFromFile(sanitizePath(source));
}

function asStringArray(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.map((x) => String(x));
}

// ---------------------------------------------------------------------------
// Tool handlers
// ---------------------------------------------------------------------------

let _lastMappingReport: MapResultReport | null = null;

/** Reset the cached "last mapping" report (used by tests). */
export function resetLastMappingReport(): void {
  _lastMappingReport = null;
}

async function handleMap(args: Record<string, unknown>): Promise<unknown> {
  const source = String(args["source"]);
  const target = String(args["target"]);
  const table = typeof args["table"] === "string" ? (args["table"] as string) : undefined;
  const minConfidence =
    typeof args["min_confidence"] === "number"
      ? (args["min_confidence"] as number)
      : 0.2;
  const domains = Array.isArray(args["domains"])
    ? asStringArray(args["domains"])
    : undefined;

  const src = await resolveSchema(source, table);
  const tgt = await resolveSchema(target, table);

  let schemaFile: SchemaInfo | undefined;
  if (typeof args["schema_file"] === "string" && args["schema_file"]) {
    schemaFile = await extractSchemaFromFile(sanitizePath(args["schema_file"] as string));
  }

  const engineOptions: ConstructorParameters<typeof MapEngine>[0] = { minConfidence };
  if (domains !== undefined) engineOptions.domains = domains;
  const engine = new MapEngine(engineOptions);

  const subOpts: Parameters<typeof engine.mapSchemas>[2] = {};
  if (schemaFile !== undefined) subOpts.schemaFile = schemaFile;
  const result = engine.mapSchemas(src, tgt, subOpts);
  return mapResultToReport(result);
}

async function handleInspect(args: Record<string, unknown>): Promise<unknown> {
  const source = String(args["source"]);
  const table = typeof args["table"] === "string" ? (args["table"] as string) : undefined;
  const schema = await resolveSchema(source, table);
  const round = (n: number, d: number): number => {
    const f = 10 ** d;
    return Math.round(n * f) / f;
  };
  return {
    source_name: schema.sourceName || source,
    field_count: schema.fields.length,
    fields: schema.fields.map((f) => ({
      name: f.name,
      dtype: f.dtype,
      null_rate: round(f.nullRate, 4),
      unique_rate: round(f.uniqueRate, 4),
      value_count: f.valueCount,
      sample_values: f.sampleValues.slice(0, 5).map((s) => String(s)),
    })),
  };
}

async function handleValidate(args: Record<string, unknown>): Promise<unknown> {
  const cfgText = await readFile(sanitizePath(args["config"]), "utf8");
  const mappingResult = fromConfig(cfgText);
  const schema = await resolveSchema(String(args["source"]), undefined);
  const sourceCols = new Set(schema.fields.map((f) => f.name));

  const missingSources = mappingResult.mappings
    .filter((m) => !sourceCols.has(m.source))
    .map((m) => m.source);
  const mappedTargets = new Set(mappingResult.mappings.map((m) => m.target));

  const requiredFields = asStringArray(args["required_fields"]);
  const missingRequired = requiredFields.filter((r) => !mappedTargets.has(r));

  return {
    all_sources_present: missingSources.length === 0,
    missing_sources: missingSources,
    required_fields_mapped: missingRequired.length === 0,
    missing_required: missingRequired,
    mapping_count: mappingResult.mappings.length,
  };
}

async function handleApply(args: Record<string, unknown>): Promise<unknown> {
  const source = sanitizePath(args["source"]);
  const output = sanitizePath(args["output"]);
  const cfgText = await readFile(sanitizePath(args["config"]), "utf8");
  const mappingResult = fromConfig(cfgText);

  if (extname(source).toLowerCase() !== ".csv") {
    throw new Error("apply currently only supports CSV sources");
  }
  const text = await readFile(source, "utf8");
  const { headers, rows } = parseCsv(text);

  const renameMap = new Map<string, string>();
  const colSet = new Set(headers);
  for (const m of mappingResult.mappings) {
    if (!colSet.has(m.source)) {
      throw new Error(`Source column missing from CSV: ${m.source}`);
    }
    renameMap.set(m.source, m.target);
  }
  const newHeaders = headers.map((h) => renameMap.get(h) ?? h);

  const escape = (v: string): string =>
    /[",\r\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
  const lines = [newHeaders.map(escape).join(",")];
  for (const row of rows) {
    lines.push(headers.map((h) => escape(row[h] ?? "")).join(","));
  }
  await writeFile(output, `${lines.join("\n")}\n`, "utf8");

  return {
    rows_written: rows.length,
    columns: newHeaders,
    output,
  };
}

type Handler = (args: Record<string, unknown>) => Promise<unknown>;

const HANDLERS: Record<string, Handler> = {
  map: handleMap,
  inspect: handleInspect,
  validate: handleValidate,
  apply: handleApply,
};

/**
 * Dispatch a single tool call. Never throws -- failures come back as
 * `{ error: "<msg>" }`. Caches the `map` report so the "Last Mapping Report"
 * resource can serve it.
 */
export async function handleTool(
  name: string,
  rawArgs: Record<string, unknown>,
): Promise<unknown> {
  const args = rawArgs ?? {};
  const handler = HANDLERS[name];
  if (!handler) {
    return { error: `Unknown tool: ${name}` };
  }
  try {
    const result = await handler(args);
    if (name === "map") {
      _lastMappingReport = result as MapResultReport;
    }
    return result;
  } catch (err) {
    return { error: err instanceof Error ? err.message : String(err) };
  }
}

// ---------------------------------------------------------------------------
// Resources
// ---------------------------------------------------------------------------

interface Resource {
  readonly uri: string;
  readonly name: string;
  readonly description: string;
  readonly mimeType: string;
}

export function listResources(): Resource[] {
  const resources: Resource[] = [
    {
      uri: "infermap://supported-domains",
      name: "Supported Domains",
      description:
        "List of available domain dictionaries (healthcare, finance, etc.)",
      mimeType: "application/json",
    },
    {
      uri: "infermap://scorer-info",
      name: "Scorer Pipeline",
      description: "Available scorers with names and weights",
      mimeType: "application/json",
    },
  ];
  if (_lastMappingReport !== null) {
    resources.push({
      uri: "infermap://last-mapping/report",
      name: "Last Mapping Report",
      description: "Full report from the most recent map operation",
      mimeType: "application/json",
    });
  }
  return resources;
}

export function readResource(uri: string): string {
  if (uri === "infermap://supported-domains") {
    return JSON.stringify({ domains: availableDomains() }, null, 2);
  }
  if (uri === "infermap://scorer-info") {
    const scorers = defaultScorers().map((s) => ({ name: s.name, weight: s.weight }));
    return JSON.stringify({ scorers }, null, 2);
  }
  if (uri === "infermap://last-mapping/report") {
    if (_lastMappingReport === null) {
      return JSON.stringify({ error: "No mapping has been run yet" });
    }
    return JSON.stringify(_lastMappingReport, null, 2);
  }
  return JSON.stringify({ error: `Unknown resource: ${uri}` });
}

// ---------------------------------------------------------------------------
// Prompts
// ---------------------------------------------------------------------------

interface PromptArgument {
  readonly name: string;
  readonly description: string;
  readonly required: boolean;
}

interface Prompt {
  readonly name: string;
  readonly description: string;
  readonly arguments: readonly PromptArgument[];
}

interface PromptMessage {
  readonly role: "user";
  readonly content: { readonly type: "text"; readonly text: string };
}

export const PROMPTS: readonly Prompt[] = [
  {
    name: "map-walkthrough",
    description:
      "Guided schema mapping workflow: inspect both sources, run mapping, review results, validate, apply.",
    arguments: [
      { name: "source", description: "Path to source data file", required: true },
      { name: "target", description: "Path to target data/schema", required: true },
    ],
  },
  {
    name: "compare-schemas",
    description:
      "Inspect two data sources side-by-side and highlight structural differences before mapping.",
    arguments: [
      { name: "source_a", description: "Path to first data source", required: true },
      { name: "source_b", description: "Path to second data source", required: true },
    ],
  },
  {
    name: "domain-mapping",
    description:
      "Map data using domain-specific dictionaries for better accuracy on industry data.",
    arguments: [
      { name: "source", description: "Path to source data", required: true },
      { name: "target", description: "Path to target schema", required: true },
      { name: "domain", description: "Domain name (e.g. 'healthcare', 'finance')", required: true },
    ],
  },
];

export function getPrompt(
  name: string,
  args: Record<string, unknown> = {},
): PromptMessage[] {
  const get = (k: string, fallback: string): string =>
    typeof args[k] === "string" && args[k] ? (args[k] as string) : fallback;

  const msg = (text: string): PromptMessage[] => [
    { role: "user", content: { type: "text", text } },
  ];

  if (name === "map-walkthrough") {
    const source = get("source", "<source>");
    const target = get("target", "<target>");
    return msg(
      `I want to map columns from '${source}' to the schema in '${target}'. Walk me through it:\n\n` +
        `1. Call \`inspect\` on '${source}' to see its fields, types, and sample values.\n` +
        `2. Call \`inspect\` on '${target}' to see the target schema.\n` +
        "3. Review both schemas and note any obvious matches or potential issues.\n" +
        `4. Call \`map\` with source='${source}' and target='${target}' to run the mapping.\n` +
        "5. Review the results — show me each mapping with its confidence score and reasoning.\n" +
        "6. Flag any low-confidence mappings or unmapped fields that need attention.\n" +
        "7. If the mapping looks good, save the config and call `validate` to check it.\n" +
        "8. Finally, call `apply` to produce the remapped output file.\n\n" +
        "Start with step 1 now.",
    );
  }

  if (name === "compare-schemas") {
    const a = get("source_a", "<source_a>");
    const b = get("source_b", "<source_b>");
    return msg(
      `Compare these two data sources side-by-side:\n\n` +
        `1. Call \`inspect\` on '${a}' to get its schema.\n` +
        `2. Call \`inspect\` on '${b}' to get its schema.\n` +
        "3. Compare them:\n" +
        "   - Which fields are likely the same (by name or type)?\n" +
        "   - Which fields exist in one but not the other?\n" +
        "   - Are there type mismatches on likely-matching fields?\n" +
        "   - Are there naming convention differences (camelCase vs snake_case, prefixes, etc.)?\n" +
        "4. Summarize in a table: source_a field → likely source_b match → confidence (high/medium/low/none).",
    );
  }

  if (name === "domain-mapping") {
    const source = get("source", "<source>");
    const target = get("target", "<target>");
    const domain = get("domain", "generic");
    return msg(
      `Map '${source}' to '${target}' using the '${domain}' domain dictionary for better accuracy:\n\n` +
        `1. Call \`inspect\` on '${source}' to see the data.\n` +
        `2. Call \`map\` with source='${source}', target='${target}', domains=['${domain}'].\n` +
        "3. Review the mappings — the domain dictionary should resolve industry-specific aliases.\n" +
        "4. Highlight which mappings were improved by the domain dictionary.\n" +
        "5. If any important fields are still unmapped, suggest additional aliases to add.",
    );
  }

  return msg(`Unknown prompt: ${name}`);
}

// ---------------------------------------------------------------------------
// JSON-RPC over stdio
// ---------------------------------------------------------------------------

interface JsonRpcRequest {
  jsonrpc?: string;
  id?: number | string | null;
  method?: string;
  params?: Record<string, unknown>;
}

function writeMessage(msg: Record<string, unknown>): void {
  process.stdout.write(JSON.stringify(msg) + "\n");
}

/**
 * Start the MCP server reading JSON-RPC messages one per line from stdin and
 * writing responses to stdout. Intended for Claude Desktop / any MCP client
 * using stdio transport.
 *
 * Unknown methods return a JSON-RPC error. Bad JSON is logged to stderr (via
 * console.warn) but does not crash the loop.
 */
export function startMcpServer(): void {
  const rl = createInterface({ input: process.stdin, terminal: false });

  rl.on("line", (line: string) => {
    if (line.trim() === "") return;
    let req: JsonRpcRequest;
    try {
      req = JSON.parse(line) as JsonRpcRequest;
    } catch (err) {
      console.warn("MCP parse error:", err instanceof Error ? err.message : String(err));
      return;
    }

    const id = req.id ?? null;

    void (async () => {
      try {
        if (req.method === "initialize") {
          writeMessage({
            jsonrpc: "2.0",
            id,
            result: {
              protocolVersion: "2024-11-05",
              serverInfo: { name: "infermap", version: "0.5.0" },
              capabilities: { tools: {}, resources: {}, prompts: {} },
            },
          });
          return;
        }

        if (req.method === "tools/list") {
          writeMessage({ jsonrpc: "2.0", id, result: { tools: TOOLS } });
          return;
        }

        if (req.method === "tools/call") {
          const params = req.params ?? {};
          const toolName = String(params["name"] ?? "");
          const toolArgs =
            (params["arguments"] as Record<string, unknown> | undefined) ?? {};
          const result = await handleTool(toolName, toolArgs);
          writeMessage({
            jsonrpc: "2.0",
            id,
            result: {
              content: [
                { type: "text", text: JSON.stringify(result, null, 2) },
              ],
            },
          });
          return;
        }

        if (req.method === "resources/list") {
          writeMessage({ jsonrpc: "2.0", id, result: { resources: listResources() } });
          return;
        }

        if (req.method === "resources/read") {
          const params = req.params ?? {};
          const uri = String(params["uri"] ?? "");
          const text = readResource(uri);
          writeMessage({
            jsonrpc: "2.0",
            id,
            result: {
              contents: [{ uri, mimeType: "application/json", text }],
            },
          });
          return;
        }

        if (req.method === "prompts/list") {
          writeMessage({ jsonrpc: "2.0", id, result: { prompts: PROMPTS } });
          return;
        }

        if (req.method === "prompts/get") {
          const params = req.params ?? {};
          const promptName = String(params["name"] ?? "");
          const promptArgs =
            (params["arguments"] as Record<string, unknown> | undefined) ?? {};
          writeMessage({
            jsonrpc: "2.0",
            id,
            result: { messages: getPrompt(promptName, promptArgs) },
          });
          return;
        }

        if (
          req.method === "notifications/initialized" ||
          req.method === "notifications/cancelled"
        ) {
          // No response to notifications.
          return;
        }

        writeMessage({
          jsonrpc: "2.0",
          id,
          error: { code: -32601, message: `Method not found: ${req.method}` },
        });
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        writeMessage({ jsonrpc: "2.0", id, error: { code: -32603, message: msg } });
      }
    })();
  });

  rl.on("close", () => {
    process.exit(0);
  });
}

// Run as a bin when invoked directly (the `infermap-mcp` entry point).
// tsup compiles this to dist/node/mcp/server.{js,cjs}; the cjs build is the bin.
const isMain = (() => {
  try {
    // CJS: require.main === module. ESM bundles set this differently, but the
    // bin entry is the cjs build, so this guard fires there.
    return typeof require !== "undefined" && require.main === module;
  } catch {
    return false;
  }
})();

if (isMain) {
  startMcpServer();
}
