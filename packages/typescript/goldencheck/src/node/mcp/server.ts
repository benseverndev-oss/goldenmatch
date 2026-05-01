/**
 * MCP server — exposes GoldenCheck tools for Claude Desktop and other MCP clients.
 * Port of goldencheck/mcp/server.py. Node-only (uses MCP SDK).
 */

import { readFile } from "../reader.js";
import { scanData, type ScanOptions } from "../../core/engine/scanner.js";
import { applyConfidenceDowngrade } from "../../core/engine/confidence.js";
import { Severity, type Finding, type DatasetProfile, healthScore } from "../../core/types.js";
import { listAvailableDomains, getDomainTypes } from "../../core/semantic/domains/index.js";

// Tool definitions for MCP registration
export const TOOL_DEFINITIONS = [
  {
    name: "scan",
    description: "Scan a data file (CSV, Parquet) for data quality issues. Returns findings with severity, confidence, affected rows.",
    inputSchema: {
      type: "object" as const,
      properties: {
        file_path: { type: "string" as const, description: "Path to the data file" },
        sample_size: { type: "integer" as const, description: "Max rows to sample", default: 100000 },
        domain: { type: "string" as const, description: "Domain pack name" },
      },
      required: ["file_path"],
    },
  },
  {
    name: "profile",
    description: "Profile a data file — column types, null%, unique%, min/max, health score.",
    inputSchema: {
      type: "object" as const,
      properties: {
        file_path: { type: "string" as const, description: "Path to the data file" },
      },
      required: ["file_path"],
    },
  },
  {
    name: "health_score",
    description: "Get health score (A-F, 0-100) for a data file.",
    inputSchema: {
      type: "object" as const,
      properties: {
        file_path: { type: "string" as const, description: "Path to the data file" },
      },
      required: ["file_path"],
    },
  },
  {
    name: "list_checks",
    description: "List all available profiler checks and what they detect.",
    inputSchema: { type: "object" as const, properties: {} },
  },
  {
    name: "get_column_detail",
    description: "Get detailed profile and findings for a specific column.",
    inputSchema: {
      type: "object" as const,
      properties: {
        file_path: { type: "string" as const, description: "Path to the data file" },
        column: { type: "string" as const, description: "Column name" },
      },
      required: ["file_path", "column"],
    },
  },
  {
    name: "list_domains",
    description: "List available domain packs (healthcare, finance, ecommerce).",
    inputSchema: { type: "object" as const, properties: {} },
  },
  {
    name: "get_domain_info",
    description: "Get info about a domain pack — types, hints, suppression rules.",
    inputSchema: {
      type: "object" as const,
      properties: {
        domain: { type: "string" as const, description: "Domain pack name" },
      },
      required: ["domain"],
    },
  },
];

// --- Tool handlers ---

function findingsByColumn(findings: readonly Finding[]): Record<string, { errors: number; warnings: number }> {
  const byCol: Record<string, { errors: number; warnings: number }> = {};
  for (const f of findings) {
    if (f.severity >= Severity.WARNING) {
      if (!byCol[f.column]) byCol[f.column] = { errors: 0, warnings: 0 };
      if (f.severity === Severity.ERROR) byCol[f.column]!.errors++;
      else byCol[f.column]!.warnings++;
    }
  }
  return byCol;
}

function serializeFindings(findings: readonly Finding[]): object[] {
  return findings.map((f) => ({
    severity: f.severity === Severity.ERROR ? "ERROR" : f.severity === Severity.WARNING ? "WARNING" : "INFO",
    column: f.column,
    check: f.check,
    message: f.message,
    affected_rows: f.affectedRows,
    sample_values: f.sampleValues,
    confidence: f.confidence,
    source: f.source,
  }));
}

export function handleTool(name: string, args: Record<string, unknown>): object {
  switch (name) {
    case "scan":
      return toolScan(args);
    case "profile":
      return toolProfile(args);
    case "health_score":
      return toolHealthScore(args);
    case "list_checks":
      return toolListChecks();
    case "get_column_detail":
      return toolGetColumnDetail(args);
    case "list_domains":
      return toolListDomains();
    case "get_domain_info":
      return toolGetDomainInfo(args);
    default:
      return { error: `Unknown tool: ${name}` };
  }
}

function toolScan(args: Record<string, unknown>): object {
  const filePath = args["file_path"] as string;
  const sampleSize = (args["sample_size"] as number) ?? 100000;
  const domain = args["domain"] as string | undefined;

  const data = readFile(filePath);
  const opts: ScanOptions = { sampleSize, domain };
  const result = scanData(data, opts);
  const findings = applyConfidenceDowngrade(result.findings, false);
  const { grade, points } = healthScore(findingsByColumn(findings));

  return {
    file: filePath,
    rows: result.profile.rowCount,
    columns: result.profile.columnCount,
    health_grade: grade,
    health_score: points,
    total_findings: findings.length,
    errors: findings.filter((f) => f.severity === Severity.ERROR).length,
    warnings: findings.filter((f) => f.severity === Severity.WARNING).length,
    findings: serializeFindings(findings),
  };
}

function toolProfile(args: Record<string, unknown>): object {
  const filePath = args["file_path"] as string;
  const data = readFile(filePath);
  const result = scanData(data);
  const findings = applyConfidenceDowngrade(result.findings, false);
  const { grade, points } = healthScore(findingsByColumn(findings));

  return {
    file: filePath,
    rows: result.profile.rowCount,
    columns_count: result.profile.columnCount,
    health_grade: grade,
    health_score: points,
    columns: result.profile.columns.map((c) => ({
      name: c.name,
      type: c.inferredType,
      null_pct: Math.round(c.nullPct * 100) / 100,
      unique_pct: Math.round(c.uniquePct * 100) / 100,
      row_count: c.rowCount,
    })),
  };
}

function toolHealthScore(args: Record<string, unknown>): object {
  const filePath = args["file_path"] as string;
  const data = readFile(filePath);
  const result = scanData(data);
  const findings = applyConfidenceDowngrade(result.findings, false);
  const { grade, points } = healthScore(findingsByColumn(findings));

  return {
    file: filePath,
    grade,
    score: points,
    errors: findings.filter((f) => f.severity === Severity.ERROR).length,
    warnings: findings.filter((f) => f.severity === Severity.WARNING).length,
  };
}

function toolListChecks(): object {
  return {
    checks: [
      { name: "type_inference", description: "Detects columns stored as wrong types" },
      { name: "nullability", description: "Identifies required vs optional columns" },
      { name: "uniqueness", description: "Finds primary key candidates and duplicates" },
      { name: "format_detection", description: "Validates email, phone, URL formats" },
      { name: "range_distribution", description: "Finds outliers in numeric columns" },
      { name: "cardinality", description: "Identifies enum candidates" },
      { name: "pattern_consistency", description: "Detects mixed formats within columns" },
      { name: "encoding_detection", description: "Detects encoding issues and control chars" },
      { name: "sequence_detection", description: "Finds gaps in sequential columns" },
      { name: "drift_detection", description: "Detects distribution shifts" },
      { name: "temporal_order", description: "Cross-column: start > end violations" },
      { name: "null_correlation", description: "Cross-column: correlated nulls" },
      { name: "cross_column_validation", description: "Cross-column: value > max violations" },
      { name: "cross_column", description: "Cross-column: age vs DOB mismatches" },
    ],
  };
}

function toolGetColumnDetail(args: Record<string, unknown>): object {
  const filePath = args["file_path"] as string;
  const columnName = args["column"] as string;
  const data = readFile(filePath);
  const result = scanData(data);
  const findings = applyConfidenceDowngrade(result.findings, false);

  const colProfile = result.profile.columns.find((c) => c.name === columnName);
  if (!colProfile) {
    return { error: `Column '${columnName}' not found. Available: ${result.profile.columns.map((c) => c.name)}` };
  }

  const colFindings = findings.filter((f) => f.column === columnName);
  return {
    column: columnName,
    type: colProfile.inferredType,
    null_pct: Math.round(colProfile.nullPct * 100) / 100,
    unique_pct: Math.round(colProfile.uniquePct * 100) / 100,
    row_count: colProfile.rowCount,
    findings: serializeFindings(colFindings),
  };
}

function toolListDomains(): object {
  return { domains: listAvailableDomains().map((name) => ({ name, source: "bundled" })) };
}

function toolGetDomainInfo(args: Record<string, unknown>): object {
  const domain = args["domain"] as string;
  const types = getDomainTypes(domain);
  if (!types) {
    return { error: `Unknown domain: '${domain}'. Available: ${listAvailableDomains().join(", ")}` };
  }
  const typesInfo: Record<string, object> = {};
  for (const [name, def] of Object.entries(types)) {
    typesInfo[name] = { name_hints: def.nameHints, suppress: def.suppress };
  }
  return { name: domain, types: typesInfo };
}
