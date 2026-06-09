#!/usr/bin/env node
/** `goldenanalysis-js` CLI. */

import { readFileSync } from "node:fs";
import { Command } from "commander";
import { analyze } from "./core/analyze.js";
import { buildNarrative } from "./core/narrative.js";
import type { RegressionPolicy } from "./core/regressions.js";
import { toJson, toMarkdown } from "./core/render.js";
import type { FrameRows } from "./core/types.js";
import { ReportHistory } from "./node/history.js";

/** Minimal CSV -> rows (empty cell => null; numeric strings => number). */
function parseCsv(text: string): FrameRows {
  const lines = text.replace(/\r\n/g, "\n").split("\n").filter((l) => l.length > 0);
  if (lines.length === 0) return [];
  const header = lines[0]!.split(",").map((h) => h.trim());
  return lines.slice(1).map((line) => {
    const cells = line.split(",");
    const row: Record<string, unknown> = {};
    header.forEach((key, i) => {
      const raw = (cells[i] ?? "").trim();
      row[key] = raw === "" ? null : Number.isNaN(Number(raw)) || raw === "" ? raw : Number(raw);
    });
    return row;
  });
}

/** Load rows from a .json (array) or .csv file. */
export function loadRows(path: string): FrameRows {
  const text = readFileSync(path, "utf-8");
  if (path.toLowerCase().endsWith(".json")) {
    const data = JSON.parse(text) as unknown;
    if (!Array.isArray(data)) throw new Error("JSON input must be an array of row objects");
    return data as FrameRows;
  }
  return parseCsv(text);
}

export interface ReportOptions {
  readonly format?: "markdown" | "json";
  readonly analyzers?: string;
}

/** Render a report from an input file path (the testable core of `report`). */
export function renderReportFromFile(path: string, options: ReportOptions = {}): string {
  const rows = loadRows(path);
  const analyzers =
    options.analyzers && options.analyzers !== "all"
      ? options.analyzers.split(",").map((s) => s.trim()).filter(Boolean)
      : undefined;
  const dataset = path.replace(/^.*[\\/]/, "").replace(/\.[^.]+$/, "");
  const report = analyze(rows, analyzers, { dataset });
  return (options.format ?? "markdown") === "json" ? toJson(report) : toMarkdown(report);
}

/** Parse `--policy` as JSON (`{defaultPct,perMetric}`) or `key=pct,...` (`*=pct`
 * sets the global default). Returns undefined for an empty/absent spec. */
export function parsePolicy(raw?: string): RegressionPolicy | undefined {
  if (!raw) return undefined;
  const trimmed = raw.trim();
  if (trimmed.startsWith("{")) {
    const obj = JSON.parse(trimmed) as Partial<RegressionPolicy>;
    return { defaultPct: obj.defaultPct ?? 10, perMetric: obj.perMetric ?? {} };
  }
  const perMetric: Record<string, number> = {};
  let defaultPct = 10;
  for (const pair of trimmed.split(",")) {
    const eq = pair.indexOf("=");
    if (eq < 0) continue;
    const key = pair.slice(0, eq).trim();
    const pct = Number(pair.slice(eq + 1).trim());
    if (Number.isNaN(pct)) continue;
    if (key === "*") defaultPct = pct;
    else if (key.length > 0) perMetric[key] = pct;
  }
  return { defaultPct, perMetric };
}

export interface TrendCliOptions {
  readonly history: string;
  readonly dataset?: string;
  readonly last?: number;
  readonly analysis?: string;
}

/** Render a metric's run-history trend (the testable core of `trend`). */
export function runTrend(metricKey: string, options: TrendCliOptions): string {
  const hist = new ReportHistory({ path: options.history });
  const series = hist.trend(metricKey, options.dataset ?? "frame", {
    lastN: options.last ?? 30,
    analysisName: options.analysis ?? "default",
  });
  const lines = [`# Trend — ${series.metricKey} (${series.dataset})`];
  for (const [runId, value] of series.points) lines.push(`${runId}\t${value}`);
  if (series.points.length === 0) lines.push("(no data)");
  return lines.join("\n") + "\n";
}

export interface RegressionsCliOptions {
  readonly history: string;
  readonly dataset?: string;
  readonly baseline?: string;
  readonly window?: number;
  readonly policy?: string;
  readonly analysis?: string;
}

export interface RegressionsCliResult {
  readonly text: string;
  readonly flaggedCount: number;
}

/** Detect regressions in the latest run vs history and render the markdown report
 * (callout + Δ column + narrative). The testable core of `regressions`. */
export function runRegressions(options: RegressionsCliOptions): RegressionsCliResult {
  const hist = new ReportHistory({ path: options.history });
  const dataset = options.dataset ?? "frame";
  const analysisName = options.analysis ?? "default";
  const policy = parsePolicy(options.policy);
  const flagged = hist.detectRegressions(dataset, {
    baseline: options.baseline ?? "rolling_median",
    window: options.window ?? 7,
    analysisName,
    ...(policy ? { policy } : {}), // omit (don't pass undefined) under exactOptionalPropertyTypes
  });
  const reports = hist.reports(dataset, { analysisName });
  const latest = reports[reports.length - 1];
  if (latest === undefined) return { text: "No reports in history.\n", flaggedCount: 0 };
  const withNarrative = { ...latest, narrative: buildNarrative(latest, flagged) };
  return { text: toMarkdown(withNarrative, flagged), flaggedCount: flagged.length };
}

export function buildProgram(): Command {
  const program = new Command();
  program.name("goldenanalysis-js").description("Measure and report across the Golden Suite.");
  program
    .command("report")
    .argument("<input>", "A .json (array of rows) or .csv file")
    .option("--format <format>", "markdown | json", "markdown")
    .option("--analyzers <list>", "comma-separated analyzer names, or 'all'", "all")
    .action((input: string, opts: { format?: "markdown" | "json"; analyzers?: string }) => {
      process.stdout.write(renderReportFromFile(input, opts) + "\n");
    });
  program
    .command("trend")
    .argument("<metric>", "metric key, e.g. cluster.singleton_ratio")
    .requiredOption("--history <path>", "path to the analysis.jsonl history")
    .option("--dataset <name>", "dataset name", "frame")
    .option("--last <n>", "trailing points to show", (v) => Number(v))
    .option("--analysis <name>", "analysis name", "default")
    .action((metric: string, opts: TrendCliOptions) => {
      process.stdout.write(runTrend(metric, opts));
    });
  program
    .command("regressions")
    .requiredOption("--history <path>", "path to the analysis.jsonl history")
    .option("--dataset <name>", "dataset name", "frame")
    .option("--baseline <strategy>", "previous | rolling_median | last_known_good | <run_id>", "rolling_median")
    .option("--window <n>", "rolling-median window", (v) => Number(v))
    .option("--policy <spec>", "JSON {defaultPct,perMetric} or key=pct,*=pct")
    .option("--analysis <name>", "analysis name", "default")
    .option("--fail-on-regression", "exit 1 when any regression is flagged", false)
    .action((opts: RegressionsCliOptions & { failOnRegression?: boolean }) => {
      const result = runRegressions(opts);
      process.stdout.write(result.text);
      if (opts.failOnRegression && result.flaggedCount > 0) process.exitCode = 1;
    });
  return program;
}

// Only run when invoked as the CLI (not when imported by tests).
if (process.argv[1] && /cli\.(c?js|ts)$/.test(process.argv[1])) {
  buildProgram().parse();
}
