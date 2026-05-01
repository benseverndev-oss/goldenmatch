/**
 * GoldenFlow MCP Server — 10 tools for data transformation via stdio transport.
 * Node-only module.
 */

import { readFileSync } from "node:fs";
import { resolve, isAbsolute } from "node:path";
import { readFile } from "../connectors/file.js";

/** Validate a file path to prevent path traversal. Resolves relative to cwd. */
function sanitizePath(raw: string): string {
  const resolved = isAbsolute(raw) ? resolve(raw) : resolve(process.cwd(), raw);
  const cwd = resolve(process.cwd());
  if (!resolved.startsWith(cwd)) {
    throw new Error(`Path '${raw}' is outside the working directory`);
  }
  return resolved;
}
import { TransformEngine } from "../../core/engine/transformer.js";
import { profileDataframe } from "../../core/engine/profiler-bridge.js";
import { diffDataframes } from "../../core/engine/differ.js";
import { learnConfig } from "../../core/config/learner.js";
import { loadConfigFromString } from "../../core/config/loader.js";
import { makeConfig } from "../../core/types.js";
import { listTransforms, getTransform } from "../../core/transforms/index.js";
import { SchemaMapper } from "../../core/mapping/schema-mapper.js";
import { selectFromFindings } from "../../core/engine/selector.js";
import { listDomains, loadDomain } from "../../core/domains/index.js";

// ---------------------------------------------------------------------------
// Tool definitions
// ---------------------------------------------------------------------------

interface ToolDefinition {
  name: string;
  description: string;
  inputSchema: {
    type: "object";
    properties: Record<string, unknown>;
    required?: string[];
  };
}

export const TOOL_DEFINITIONS: ToolDefinition[] = [
  {
    name: "transform",
    description: "Transform a data file. Reads a CSV, applies transforms (auto or from config), and returns the manifest.",
    inputSchema: {
      type: "object",
      properties: {
        path: { type: "string", description: "Path to the data file (CSV)." },
        config: { type: "string", description: "Optional path to a YAML config file." },
      },
      required: ["path"],
    },
  },
  {
    name: "map",
    description: "Auto-map schemas between two data files. Returns column mappings with confidence scores.",
    inputSchema: {
      type: "object",
      properties: {
        source: { type: "string", description: "Path to the source data file." },
        target: { type: "string", description: "Path to the target data file." },
      },
      required: ["source", "target"],
    },
  },
  {
    name: "profile",
    description: "Profile a data file. Returns column types, stats, and quality indicators.",
    inputSchema: {
      type: "object",
      properties: {
        path: { type: "string", description: "Path to the data file (CSV)." },
      },
      required: ["path"],
    },
  },
  {
    name: "learn",
    description: "Generate a YAML config from data patterns. Analyzes the file and returns a recommended config.",
    inputSchema: {
      type: "object",
      properties: {
        path: { type: "string", description: "Path to the data file (CSV)." },
      },
      required: ["path"],
    },
  },
  {
    name: "diff",
    description: "Compare two data files and report differences (added/removed/changed columns and rows).",
    inputSchema: {
      type: "object",
      properties: {
        path_before: { type: "string", description: "Path to the before file." },
        path_after: { type: "string", description: "Path to the after file." },
      },
      required: ["path_before", "path_after"],
    },
  },
  {
    name: "validate",
    description: "Dry-run transform. Shows what transforms would be applied without writing output.",
    inputSchema: {
      type: "object",
      properties: {
        path: { type: "string", description: "Path to the data file (CSV)." },
        config: { type: "string", description: "Optional path to a YAML config file." },
      },
      required: ["path"],
    },
  },
  {
    name: "list_transforms",
    description: "List all available transforms with their metadata (name, input types, mode, priority).",
    inputSchema: {
      type: "object",
      properties: {},
    },
  },
  {
    name: "explain_transform",
    description: "Describe a specific transform by name. Returns its metadata or an error if not found.",
    inputSchema: {
      type: "object",
      properties: {
        transform_name: { type: "string", description: "Name of the transform to describe." },
      },
      required: ["transform_name"],
    },
  },
  {
    name: "list_domains",
    description: "List all available domain packs (e.g. people_hr, healthcare, finance).",
    inputSchema: {
      type: "object",
      properties: {},
    },
  },
  {
    name: "select_from_findings",
    description: "Map GoldenCheck findings to recommended GoldenFlow transforms.",
    inputSchema: {
      type: "object",
      properties: {
        findings: {
          type: "array",
          items: { type: "object" },
          description: "Array of GoldenCheck finding objects (each with 'check' and 'column' keys).",
        },
      },
      required: ["findings"],
    },
  },
];

// ---------------------------------------------------------------------------
// Tool handler
// ---------------------------------------------------------------------------

export function handleTool(name: string, arguments_: Record<string, unknown>): string {
  try {
    return _handleToolInner(name, arguments_);
  } catch (e) {
    return JSON.stringify({ error: e instanceof Error ? e.message : String(e) });
  }
}

function _handleToolInner(name: string, arguments_: Record<string, unknown>): string {
  switch (name) {
    case "transform": {
      const path = sanitizePath(String(arguments_["path"]));
      const rows = readFile(path);
      let engine: TransformEngine;

      if (arguments_["config"]) {
        const configPath = sanitizePath(String(arguments_["config"]));
        const configContent = readFileSync(configPath, "utf-8");
        const config = loadConfigFromString(configContent);
        engine = new TransformEngine(config);
      } else {
        engine = new TransformEngine();
      }

      const result = engine.transformDf(rows, path);
      return JSON.stringify({
        rows: result.rows.length,
        transforms_applied: result.manifest.records.length,
        manifest: result.manifest,
      });
    }

    case "map": {
      const sourceRows = readFile(sanitizePath(String(arguments_["source"])));
      const targetRows = readFile(sanitizePath(String(arguments_["target"])));
      const mapper = new SchemaMapper();
      const mappings = mapper.map(sourceRows, targetRows);
      return JSON.stringify({
        mappings: mappings.map((m) => ({
          source: m.source,
          target: m.target,
          confidence: m.confidence,
        })),
      });
    }

    case "profile": {
      const profPath = sanitizePath(String(arguments_["path"]));
      const rows = readFile(profPath);
      const profile = profileDataframe(rows, profPath);
      return JSON.stringify({
        source: profile.filePath,
        row_count: profile.rowCount,
        columns: profile.columns.map((c) => ({
          name: c.name,
          type: c.inferredType,
          null_count: c.nullCount,
          unique_count: c.uniqueCount,
          sample_values: c.sampleValues,
        })),
      });
    }

    case "learn": {
      const learnPath = sanitizePath(String(arguments_["path"]));
      const rows = readFile(learnPath);
      const config = learnConfig(rows, learnPath);
      return JSON.stringify(config);
    }

    case "diff": {
      const beforeRows = readFile(sanitizePath(String(arguments_["path_before"])));
      const afterRows = readFile(sanitizePath(String(arguments_["path_after"])));
      const result = diffDataframes(beforeRows, afterRows);
      return JSON.stringify(result);
    }

    case "validate": {
      const valPath = sanitizePath(String(arguments_["path"]));
      const rows = readFile(valPath);
      let engine: TransformEngine;

      if (arguments_["config"]) {
        const configPath = sanitizePath(String(arguments_["config"]));
        const configContent = readFileSync(configPath, "utf-8");
        const config = loadConfigFromString(configContent);
        engine = new TransformEngine(config);
      } else {
        engine = new TransformEngine();
      }

      const result = engine.transformDf(rows, valPath);
      return JSON.stringify({
        mode: "would_apply",
        rows: result.rows.length,
        transforms_applied: result.manifest.records.length,
        manifest: result.manifest,
      });
    }

    case "list_transforms": {
      const transforms = listTransforms();
      return JSON.stringify(
        transforms.map((t) => ({
          name: t.name,
          input_types: t.inputTypes,
          auto_apply: t.autoApply,
          priority: t.priority,
          mode: t.mode,
        })),
      );
    }

    case "explain_transform": {
      const transformName = String(arguments_["transform_name"]);
      const info = getTransform(transformName);
      if (!info) {
        return JSON.stringify({ error: `Transform '${transformName}' not found.` });
      }
      return JSON.stringify({
        name: info.name,
        input_types: info.inputTypes,
        auto_apply: info.autoApply,
        priority: info.priority,
        mode: info.mode,
      });
    }

    case "list_domains": {
      const domains = listDomains();
      return JSON.stringify({ domains });
    }

    case "select_from_findings": {
      const findings = arguments_["findings"] as Record<string, unknown>[];
      const result = selectFromFindings(findings);
      return JSON.stringify(result);
    }

    default:
      return JSON.stringify({ error: `Unknown tool: ${name}` });
  }
}
