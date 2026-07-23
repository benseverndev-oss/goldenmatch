#!/usr/bin/env node
/**
 * MCP server — exposes the read-only GoldenAnalysis tools for Claude Desktop and
 * other MCP clients. Port of goldenanalysis/mcp/server.py. Node-only: hand-rolled
 * JSON-RPC 2.0 over stdio (node:readline; no MCP SDK dep), mirroring the sibling
 * goldencheck / goldenmatch TS servers.
 *
 * The four tools wrap engines that already exist in this package:
 *   list_analyzers      -> core/registry.availableAnalyzers
 *   analyze_frame       -> core/analyze.analyze (+ core/render)
 *   get_trend           -> node/history.ReportHistory.trend
 *   detect_regressions  -> node/history.ReportHistory.detectRegressions
 */

import { createInterface } from "node:readline";
import { readFileSync } from "node:fs";
import { analyze } from "../../core/analyze.js";
import { availableAnalyzers } from "../../core/registry.js";
import { toJson, toMarkdown } from "../../core/render.js";
import type { AnalysisReport, FrameRows } from "../../core/types.js";
import type { RegressionPolicy } from "../../core/regressions.js";
import { ReportHistory } from "../history.js";

// Local Tool shape. Annotating the array with this (rather than importing a type
// from elsewhere) keeps the dts bundler from namespace-importing above the
// shebang (rollup-plugin-dts: "Syntax not yet supported").
interface Tool {
  readonly name: string;
  readonly description: string;
  readonly inputSchema: Readonly<Record<string, unknown>>;
}

export const TOOL_DEFINITIONS: readonly Tool[] = [
  {
    name: "list_analyzers",
    description: "List the discoverable GoldenAnalysis analyzers.",
    inputSchema: { type: "object" as const, properties: {} },
  },
  {
    name: "analyze_frame",
    description:
      "Analyze a .csv frame (or re-render a saved .json AnalysisReport) into a " +
      "metrics report. (.parquet input requires the Python package — this " +
      "edge-safe port ships no Parquet reader.)",
    inputSchema: {
      type: "object" as const,
      properties: {
        path: { type: "string" as const, description: "A .csv frame or a .json AnalysisReport" },
        analyzers: { type: "string" as const, description: "Comma-separated analyzer names, or 'all'" },
        output_format: { type: "string" as const, enum: ["json", "markdown"] },
      },
      required: ["path"],
    },
  },
  {
    name: "get_trend",
    description: "Trend a metric over a run history (.jsonl/.db ReportHistory).",
    inputSchema: {
      type: "object" as const,
      properties: {
        history: { type: "string" as const },
        metric: { type: "string" as const },
        dataset: { type: "string" as const },
        last: { type: "integer" as const },
      },
      required: ["history", "metric", "dataset"],
    },
  },
  {
    name: "detect_regressions",
    description: "Detect metric regressions vs a baseline over a run history.",
    inputSchema: {
      type: "object" as const,
      properties: {
        history: { type: "string" as const },
        dataset: { type: "string" as const },
        baseline: { type: "string" as const },
        window: { type: "integer" as const },
        policy: { type: "object" as const },
      },
      required: ["history", "dataset"],
    },
  },
];

// --- CSV -> rows (empty cell => null; numeric strings => number). Mirrors the
// parser in cli.ts; kept local so the MCP bin doesn't pull in commander. ---
function parseCsvRows(text: string): FrameRows {
  const lines = text.replace(/\r\n/g, "\n").split("\n").filter((l) => l.length > 0);
  if (lines.length === 0) return [];
  const header = lines[0]!.split(",").map((h) => h.trim());
  return lines.slice(1).map((line) => {
    const cells = line.split(",");
    const row: Record<string, unknown> = {};
    header.forEach((key, i) => {
      const raw = (cells[i] ?? "").trim();
      row[key] = raw === "" ? null : Number.isNaN(Number(raw)) ? raw : Number(raw);
    });
    return row;
  });
}

// ---------------------------------------------------------------------------
// Tool handlers (return plain objects; the server wraps them as text content).
// ---------------------------------------------------------------------------

function toolListAnalyzers(): object {
  return { analyzers: availableAnalyzers() };
}

function toolAnalyzeFrame(args: Record<string, unknown>): object {
  const path = String(args["path"] ?? "");
  const outputFormat = String(args["output_format"] ?? "json");
  const lower = path.toLowerCase();

  let report: AnalysisReport;
  if (lower.endsWith(".json")) {
    // A saved AnalysisReport (mirrors the Python .json path). AnalysisReport is a
    // plain interface, so a report emitted by toJson round-trips via JSON.parse.
    report = JSON.parse(readFileSync(path, "utf-8")) as AnalysisReport;
  } else if (lower.endsWith(".csv")) {
    const rows = parseCsvRows(readFileSync(path, "utf-8"));
    const analyzersArg = args["analyzers"];
    const analyzers =
      analyzersArg && analyzersArg !== "all"
        ? String(analyzersArg).split(",").map((s) => s.trim()).filter(Boolean)
        : undefined;
    const dataset = path.replace(/^.*[\\/]/, "").replace(/\.[^.]+$/, "");
    report = analyze(rows, analyzers, { dataset });
  } else {
    return {
      error: `unsupported input type: '${path.slice(path.lastIndexOf("."))}' (want .csv/.json; .parquet needs the Python package)`,
    };
  }

  if (outputFormat === "markdown") return { markdown: toMarkdown(report) };
  return JSON.parse(toJson(report)) as object;
}

function toolGetTrend(args: Record<string, unknown>): object {
  const hist = new ReportHistory({ path: String(args["history"] ?? "") });
  const series = hist.trend(String(args["metric"] ?? ""), String(args["dataset"] ?? "frame"), {
    lastN: args["last"] !== undefined ? Number(args["last"]) : 30,
  });
  // snake_case wire shape mirrors the Python get_trend tool.
  return {
    metric_key: series.metricKey,
    dataset: series.dataset,
    points: series.points.map(([runId, value]) => [runId, value]),
  };
}

function toolDetectRegressions(args: Record<string, unknown>): object {
  const hist = new ReportHistory({ path: String(args["history"] ?? "") });
  const rawPolicy = args["policy"] as Record<string, unknown> | undefined;
  const policy: RegressionPolicy | undefined = rawPolicy
    ? {
        defaultPct: Number(rawPolicy["default_pct"] ?? 10),
        perMetric: (rawPolicy["per_metric"] as Record<string, number> | undefined) ?? {},
      }
    : undefined;
  const flagged = hist.detectRegressions(String(args["dataset"] ?? "frame"), {
    baseline: String(args["baseline"] ?? "rolling_median"),
    window: args["window"] !== undefined ? Number(args["window"]) : 7,
    ...(policy ? { policy } : {}), // omit (don't spread undefined) under exactOptionalPropertyTypes
  });
  return {
    flagged: flagged.map((r) => ({
      metric: r.metric,
      baseline: r.baseline,
      current: r.current,
      delta_pct: r.deltaPct,
      direction: r.direction,
    })),
  };
}

export function handleTool(name: string, args: Record<string, unknown>): object {
  switch (name) {
    case "list_analyzers":
      return toolListAnalyzers();
    case "analyze_frame":
      return toolAnalyzeFrame(args);
    case "get_trend":
      return toolGetTrend(args);
    case "detect_regressions":
      return toolDetectRegressions(args);
    default:
      return { error: `Unknown tool: ${name}` };
  }
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
 * writing responses to stdout (stdio transport for Claude Desktop / any MCP
 * client). `handleTool` returns an object, serialized as the text content.
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
    try {
      if (req.method === "initialize") {
        writeMessage({
          jsonrpc: "2.0",
          id,
          result: {
            protocolVersion: "2024-11-05",
            serverInfo: { name: "goldenanalysis", version: "0.1.0" },
            capabilities: { tools: {} },
          },
        });
        return;
      }

      if (req.method === "tools/list") {
        writeMessage({ jsonrpc: "2.0", id, result: { tools: TOOL_DEFINITIONS } });
        return;
      }

      if (req.method === "tools/call") {
        const params = req.params ?? {};
        const toolName = String(params["name"] ?? "");
        const toolArgs = (params["arguments"] as Record<string, unknown> | undefined) ?? {};
        const result = handleTool(toolName, toolArgs);
        writeMessage({
          jsonrpc: "2.0",
          id,
          result: { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] },
        });
        return;
      }

      if (
        req.method === "notifications/initialized" ||
        req.method === "notifications/cancelled"
      ) {
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
  });

  rl.on("close", () => {
    process.exit(0);
  });
}

// Run as a bin when invoked directly (the `goldenanalysis-mcp` entry point).
const isMain = (() => {
  try {
    return typeof require !== "undefined" && require.main === module;
  } catch {
    return false;
  }
})();

if (isMain) {
  startMcpServer();
}
