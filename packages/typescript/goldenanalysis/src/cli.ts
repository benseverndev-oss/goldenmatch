#!/usr/bin/env node
/** `goldenanalysis-js` CLI. */

import { readFileSync } from "node:fs";
import { Command } from "commander";
import { analyze } from "./core/analyze.js";
import { toJson, toMarkdown } from "./core/render.js";
import type { FrameRows } from "./core/types.js";

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
  return program;
}

// Only run when invoked as the CLI (not when imported by tests).
if (process.argv[1] && /cli\.(c?js|ts)$/.test(process.argv[1])) {
  buildProgram().parse();
}
