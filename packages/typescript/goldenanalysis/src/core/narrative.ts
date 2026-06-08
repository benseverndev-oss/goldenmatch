/**
 * Narrative generation — one-paragraph NL summary from the flagged regressions +
 * the largest co-moving metrics. Deterministic, ASCII-clean. Parity with the Python
 * `narrative.py`.
 */

import type { Regression } from "./regressions.js";
import type { AnalysisReport } from "./types.js";

function pretty(key: string): string {
  const last = key.split(".").pop() ?? key;
  return last.replace(/_/g, " ");
}

function capitalize(text: string): string {
  return text.length === 0 ? text : text[0]!.toUpperCase() + text.slice(1);
}

function num(value: number): string {
  return String(value);
}

function topFindingClass(report: AnalysisReport): readonly [string, number] | null {
  for (const table of report.tables) {
    if (table.name === "findings_by_class" && table.rows.length > 0) {
      let best: readonly unknown[] = table.rows[0]!;
      for (const row of table.rows) {
        const c = typeof row[1] === "number" ? row[1] : 0;
        const b = typeof best[1] === "number" ? best[1] : 0;
        if (c > b) best = row;
      }
      return [String(best[0]), Number(best[1])] as const;
    }
  }
  return null;
}

export function buildNarrative(report: AnalysisReport, regressions: readonly Regression[] = []): string {
  const flagged = regressions.filter((r) => r.flagged);

  if (flagged.length === 0) {
    const notable = report.metrics
      .filter((m) => typeof m.value === "number")
      .slice()
      .sort((a, b) => Math.abs(Number(b.value)) - Math.abs(Number(a.value)))
      .slice(0, 3);
    if (notable.length === 0) return "No metrics to summarize.";
    const bits = notable.map((m) => `${pretty(m.key)} = ${m.value}`).join(", ");
    return `No regressions flagged. Notable metrics: ${bits}.`;
  }

  let worst = flagged[0]!;
  for (const r of flagged) {
    if (Math.abs(r.deltaPct) > Math.abs(worst.deltaPct)) worst = r;
  }
  const dir = worst.deltaPct < 0 ? "fell" : "rose";
  const lead = `${capitalize(pretty(worst.metric))} ${dir} to ${num(worst.current)} (baseline ${num(worst.baseline)}; ${worst.deltaPct >= 0 ? "+" : ""}${worst.deltaPct.toFixed(1)}%).`;

  const parts = [lead];
  const comovers = flagged.filter((r) => r.metric !== worst.metric);
  if (comovers.length > 0) {
    const moves = comovers
      .map((r) => `${pretty(r.metric)} ${num(r.baseline)} -> ${num(r.current)} (${r.deltaPct >= 0 ? "+" : ""}${r.deltaPct.toFixed(1)}%)`)
      .join("; ");
    parts.push(`Co-moving signals: ${moves}.`);
  }
  const fc = topFindingClass(report);
  if (fc !== null) parts.push(`Most common quality finding: ${fc[0]} (${fc[1]}).`);
  return parts.join(" ");
}
