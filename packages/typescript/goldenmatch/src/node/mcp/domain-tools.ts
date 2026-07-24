/**
 * domain-tools.ts -- MCP tools for user-defined domain rulebooks.
 *
 * Ports the three Python domain tools (`_tool_list_domains`,
 * `_tool_create_domain`, `_tool_test_domain` in `goldenmatch/mcp/server.py`)
 * onto the TS registry: pure logic in `core/domain-rulebook.ts`, YAML +
 * filesystem in `node/domain-registry.ts`.
 *
 * `test_domain` reads the CURRENT RUN's rows from `RUN_STORE` -- the TS analogue
 * of Python's server-held `_rows` (populated there by `--file` at startup, here
 * by a `dedupe` call in the session).
 */

import type { Tool } from "./run-tools.js";
import { compileRulebook, extractWithRulebook } from "../../core/domain-rulebook.js";
import { makeRulebook } from "../../core/domain-rulebook.js";
import { discoverRulebooks, saveRulebook } from "../domain-registry.js";
import { RUN_STORE } from "./run-store.js";
import { homedir } from "node:os";
import { join, resolve } from "node:path";

export const DOMAIN_TOOLS: readonly Tool[] = [
  {
    name: "list_domains",
    description:
      "List available domain extraction rulebooks (user-defined YAML in " +
      ".goldenmatch/domains or ~/.goldenmatch/domains).",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "create_domain",
    description:
      "Create a custom domain extraction rulebook. Define patterns for a " +
      "specific data domain (medical devices, automotive parts, real estate, etc.).",
    inputSchema: {
      type: "object",
      properties: {
        name: { type: "string", description: "Domain name" },
        scope: {
          type: "string",
          enum: ["local", "global"],
          description: "local = .goldenmatch/domains, global = ~/.goldenmatch/domains",
        },
        signals: {
          type: "array",
          items: { type: "string" },
          description: "Column-name substrings that trigger this domain",
        },
        identifier_patterns: {
          type: "object",
          description: "name -> regex for identifier/model/SKU extraction",
        },
        brand_patterns: {
          type: "array",
          items: { type: "string" },
          description: "Literal brand names",
        },
        attribute_patterns: {
          type: "object",
          description: "name -> regex for domain-specific attributes",
        },
        stop_words: {
          type: "array",
          items: { type: "string" },
          description: "Words stripped during name normalization",
        },
      },
      required: ["name"],
    },
  },
  {
    name: "test_domain",
    description:
      "Test a domain extraction rulebook against the loaded data. Shows what " +
      "features would be extracted from sample records.",
    inputSchema: {
      type: "object",
      properties: {
        domain_name: { type: "string", description: "Rulebook name to test" },
        sample_size: { type: "number", description: "Rows to sample (default 10)" },
      },
      required: ["domain_name"],
    },
  },
];

export const DOMAIN_TOOL_NAMES: ReadonlySet<string> = new Set(
  DOMAIN_TOOLS.map((t) => t.name),
);

function strArray(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

function strRecord(v: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (v && typeof v === "object" && !Array.isArray(v)) {
    for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
      if (typeof val === "string") out[k] = val;
    }
  }
  return out;
}

function toolListDomains(): unknown {
  const rulebooks = discoverRulebooks();
  const domains = [...rulebooks.values()].map((rb) => ({
    name: rb.name,
    signals: [...rb.signals],
    identifier_patterns: Object.keys(rb.identifierPatterns),
    brand_count: rb.brandPatterns.length,
    attribute_patterns: Object.keys(rb.attributePatterns),
  }));
  return { domains, count: domains.length };
}

function toolCreateDomain(args: Record<string, unknown>): unknown {
  const name = typeof args["name"] === "string" ? args["name"] : "";
  if (!name) return { error: "create_domain requires a 'name'." };
  const scope = args["scope"] === "global" ? "global" : "local";
  const dir =
    scope === "global"
      ? join(homedir(), ".goldenmatch", "domains")
      : resolve(".goldenmatch", "domains");

  const rb = makeRulebook(name, {
    signals: strArray(args["signals"]),
    identifierPatterns: strRecord(args["identifier_patterns"]),
    brandPatterns: strArray(args["brand_patterns"]),
    attributePatterns: strRecord(args["attribute_patterns"]),
    stopWords: strArray(args["stop_words"]),
  });

  const path = saveRulebook(rb, join(dir, `${name}.yaml`));
  return {
    status: "created",
    name,
    path,
    scope,
    signals: [...rb.signals],
    identifier_patterns: Object.keys(rb.identifierPatterns),
  };
}

function toolTestDomain(args: Record<string, unknown>): unknown {
  const domainName = typeof args["domain_name"] === "string" ? args["domain_name"] : "";
  const sampleSize =
    typeof args["sample_size"] === "number" && Number.isFinite(args["sample_size"])
      ? Math.floor(args["sample_size"])
      : 10;

  const run = RUN_STORE.getCurrent();
  if (!run) {
    return { error: "No data loaded. Run dedupe (or find_duplicates) in this session first." };
  }
  const rows = [...run.rowsById.values()];
  if (rows.length === 0) return { error: "No data loaded." };

  const rulebooks = discoverRulebooks();
  const rb = rulebooks.get(domainName);
  if (!rb) {
    return {
      error: `Domain '${domainName}' not found. Available: ${[...rulebooks.keys()].join(", ")}`,
    };
  }

  // First non-internal string column, mirroring Python's sample_cols pick.
  const first = rows[0]!;
  const textCol = Object.keys(first).find(
    (c) => !c.startsWith("__") && typeof first[c] === "string",
  );
  if (!textCol) return { error: "No text columns found in data." };

  const compiled = compileRulebook(rb);
  const extractions = rows.slice(0, sampleSize).map((row) => {
    const text = String(row[textCol] ?? "");
    const ex = extractWithRulebook(compiled, text);
    return {
      original: text.slice(0, 100),
      brand: ex.brand,
      identifiers: ex.identifiers,
      name_normalized: ex.nameNormalized,
      confidence: Math.round(ex.confidence * 100) / 100,
    };
  });

  return {
    domain: domainName,
    text_column: textCol,
    sample_size: extractions.length,
    extractions,
  };
}

export function handleDomainTool(name: string, args: Record<string, unknown>): unknown {
  switch (name) {
    case "list_domains":
      return toolListDomains();
    case "create_domain":
      return toolCreateDomain(args);
    case "test_domain":
      return toolTestDomain(args);
    default:
      return { error: `Unknown domain tool: ${name}` };
  }
}
