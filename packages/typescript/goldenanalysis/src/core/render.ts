/** Report exporters — JSON + Markdown (mirrors the Python output shape). */

import type { AnalysisReport, Metric } from "./types.js";

export function toJson(report: AnalysisReport, indent = 2): string {
  return JSON.stringify(report, null, indent);
}

function fmtValue(value: number | string, unit?: string | null): string {
  let text: string;
  if (typeof value === "number") {
    text = Number.isInteger(value) ? value.toLocaleString("en-US") : String(value);
  } else {
    text = value;
  }
  return unit ? `${text} ${unit}` : text;
}

export function toMarkdown(report: AnalysisReport): string {
  const dataset = report.source["dataset"] ?? "frame";
  const lines: string[] = [`# Analysis — ${dataset} (run ${report.run_id})`, ""];

  if (report.narrative) {
    lines.push(report.narrative, "");
  }

  lines.push("| Metric | Value |", "|---|---|");
  for (const m of report.metrics as readonly Metric[]) {
    lines.push(`| ${m.key} | ${fmtValue(m.value, m.unit)} |`);
  }
  lines.push("");

  for (const table of report.tables) {
    lines.push(`**${table.name}**`, "");
    lines.push(`| ${table.columns.join(" | ")} |`);
    lines.push(`|${table.columns.map(() => "---").join("|")}|`);
    for (const row of table.rows) {
      lines.push(`| ${row.map((c) => String(c)).join(" | ")} |`);
    }
    lines.push("");
  }

  return lines.join("\n").replace(/\s+$/, "") + "\n";
}
