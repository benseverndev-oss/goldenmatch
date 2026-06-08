/** Report exporters — JSON + Markdown (mirrors the Python output shape). */

import type { Regression } from "./regressions.js";
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

/** Python `:+.1f` — always-signed, one decimal (`+2.0%` / `-8.2%`). */
function fmtDelta(pct: number): string {
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

/**
 * Markdown report. With `regressions` (Phase 2b/3b) a flagged-regression callout and
 * a `Δ vs baseline` column are added; with the default empty list the output is
 * byte-identical to the Phase 3a form (existing parity tests stay green).
 */
export function toMarkdown(report: AnalysisReport, regressions: readonly Regression[] = []): string {
  const dataset = report.source["dataset"] ?? "frame";
  const lines: string[] = [`# Analysis — ${dataset} (run ${report.run_id})`, ""];

  const byMetric = new Map<string, Regression>();
  for (const r of regressions) byMetric.set(r.metric, r);
  const flagged = regressions.filter((r) => r.flagged);
  if (flagged.length > 0) {
    const lead = flagged
      .map((r) => `${r.metric} ${r.baseline} -> ${r.current} (${fmtDelta(r.deltaPct)})`)
      .join("; ");
    lines.push(`> WARNING: ${flagged.length} regression(s) flagged. ${lead}`, "");
  }

  if (report.narrative) {
    lines.push(report.narrative, "");
  }

  if (byMetric.size > 0) {
    lines.push("| Metric | Value | Δ vs baseline |", "|---|---|---|");
    for (const m of report.metrics as readonly Metric[]) {
      const reg = byMetric.get(m.key);
      let delta = "";
      if (reg !== undefined) {
        const mark = reg.flagged ? "🔴 " : "";
        delta = `${mark}${fmtDelta(reg.deltaPct)}`;
      }
      lines.push(`| ${m.key} | ${fmtValue(m.value, m.unit)} | ${delta} |`);
    }
  } else {
    lines.push("| Metric | Value |", "|---|---|");
    for (const m of report.metrics as readonly Metric[]) {
      lines.push(`| ${m.key} | ${fmtValue(m.value, m.unit)} |`);
    }
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

  // `.trimEnd()` (not a `/\s+$/` regex) strips trailing whitespace incl. newlines
  // with no polynomial-ReDoS risk (CodeQL js/polynomial-redos).
  return lines.join("\n").trimEnd() + "\n";
}
