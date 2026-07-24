#!/usr/bin/env node
/**
 * GoldenCheck CLI — TypeScript port.
 * Port of goldencheck/cli/main.py using Commander.js.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { Command } from "commander";
import { readFile } from "./node/reader.js";
import { scanData } from "./core/engine/scanner.js";
import { applyConfidenceDowngrade } from "./core/engine/confidence.js";
import { Severity, healthScore, type Finding } from "./core/types.js";
import { reportJson } from "./core/reporters/json.js";
import { ciCheck } from "./core/reporters/ci.js";
import { listAvailableDomains } from "./core/semantic/domains/index.js";
import { recordScan, loadHistory } from "./core/engine/history.js";
import { evaluateScan, type ExpectedFinding } from "./core/engine/evaluate.js";

export const program = new Command();

program
  .name("goldencheck-js")
  .description("Data validation that discovers rules from your data")
  .version("0.6.0");

// --- scan ---
program
  .command("scan <file>")
  .description("Scan a file for data quality issues")
  .option("--domain <domain>", "Domain pack (healthcare, finance, ecommerce)")
  .option("--json", "Output as JSON")
  .option("--sample-size <n>", "Sample size", "100000")
  .option("--no-tui", "CLI output (no TUI)")
  .option("--llm-boost", "Enhance with LLM analysis")
  .option("--baseline <path>", "Baseline file for drift detection")
  .option("--no-history", "Don't record this scan in history")
  .action((file: string, opts: Record<string, unknown>) => {
    const data = readFile(file);
    const result = scanData(data, {
      sampleSize: Number(opts["sampleSize"] ?? 100000),
      domain: opts["domain"] as string | undefined,
    });
    const findings = applyConfidenceDowngrade(result.findings, false);

    // Record the scan in .goldencheck/history.jsonl (default on; --no-history
    // opts out). Commander sets `history` false when --no-history is passed.
    if (opts["history"] !== false) {
      recordScan(file, result.profile, findings);
    }

    if (opts["json"]) {
      console.log(reportJson(findings, result.profile));
    } else {
      printFindings(findings, result.profile.rowCount, result.profile.columnCount);
    }
  });

// --- validate ---
program
  .command("validate <file>")
  .description("Validate against goldencheck.yml rules")
  .option("--config <path>", "Config file path", "goldencheck.yml")
  .action((file: string, opts: Record<string, unknown>) => {
    const { readFileSync } = require("node:fs");
    const { validateConfig } = require("./core/config/schema.js");
    const { validateData } = require("./core/engine/validator.js");
    const yaml = require("yaml");

    const configPath = opts["config"] as string;
    const rawYaml = readFileSync(configPath, "utf-8");
    const config = validateConfig(yaml.parse(rawYaml));
    const data = readFile(file);
    const findings = validateData(data, config);
    printFindings(findings, data.rowCount, data.columns.length);
    process.exit(ciCheck(findings, config.settings.failOn));
  });

// --- profile ---
program
  .command("profile <file>")
  .description("Show column-level statistics")
  .option("--sample-size <n>", "Sample size", "100000")
  .action((file: string, opts: Record<string, unknown>) => {
    const data = readFile(file);
    const result = scanData(data, { sampleSize: Number(opts["sampleSize"] ?? 100000) });

    console.log(`\nProfile: ${file} — ${result.profile.rowCount} rows, ${result.profile.columnCount} columns\n`);
    for (const col of result.profile.columns) {
      console.log(`  ${col.name.padEnd(25)} ${col.inferredType.padEnd(10)} null: ${(col.nullPct * 100).toFixed(1)}%  unique: ${(col.uniquePct * 100).toFixed(1)}%`);
    }
  });

// --- health-score ---
program
  .command("health-score <file>")
  .description("Get health grade (A-F) and score (0-100)")
  .action((file: string) => {
    const data = readFile(file);
    const result = scanData(data);
    const findings = applyConfidenceDowngrade(result.findings, false);
    const byCol = findingsByColumn(findings);
    const { grade, points } = healthScore(byCol);
    console.log(`${grade} (${points}/100)`);
  });

// --- baseline ---
program
  .command("baseline <file>")
  .description("Create a statistical baseline for drift detection")
  .option("--output <path>", "Output path", "goldencheck_baseline.json")
  .action((file: string, opts: Record<string, unknown>) => {
    const { writeFileSync } = require("node:fs");
    const data = readFile(file);

    try {
      const { createBaseline } = require("./core/baseline/index.js");
      const { serializeBaseline } = require("./core/baseline/models.js");
      const baseline = createBaseline(data);
      const outPath = opts["output"] as string;
      writeFileSync(outPath, serializeBaseline(baseline));
      console.log(`Baseline saved to ${outPath}`);
    } catch (e) {
      console.error("Baseline creation failed:", e instanceof Error ? e.message : String(e));
      process.exit(1);
    }
  });

// --- fix ---
program
  .command("fix <file>")
  .description("Auto-fix data quality issues")
  .option("--mode <mode>", "Fix mode: safe, moderate, aggressive", "safe")
  .option("--dry-run", "Preview fixes without applying")
  .action((file: string, opts: Record<string, unknown>) => {
    const { applyFixes } = require("./core/engine/fixer.js");
    const data = readFile(file);
    const result = scanData(data);
    const mode = opts["mode"] as string;
    const force = mode === "aggressive";
    const { report } = applyFixes(data, result.findings, mode, force);

    console.log(`\nFix Report (${mode} mode):`);
    for (const entry of report.entries) {
      console.log(`  ${entry.column}: ${entry.fixType} (${entry.rowsAffected} rows)`);
    }
    console.log(`\nTotal fixes: ${report.entries.length}`);

    if (opts["dryRun"]) {
      console.log("(dry run — no changes written)");
    }
  });

// --- diff ---
program
  .command("diff <old> [new]")
  .description("Compare two data files")
  .action((oldFile: string, newFile?: string) => {
    const { diffData, formatDiffReport } = require("./core/engine/differ.js");
    const oldData = readFile(oldFile);
    const newData = readFile(newFile ?? oldFile);
    const oldResult = scanData(oldData);
    const newResult = scanData(newData);
    const report = diffData(oldData, newData, oldResult.findings, newResult.findings);
    console.log(formatDiffReport(report));
  });

// --- watch ---
program
  .command("watch <dir>")
  .description("Watch a directory for file changes")
  .option("--interval <seconds>", "Poll interval in seconds", "30")
  .option("--exit-on <severity>", "Exit on error or warning")
  .action(async (dir: string, opts: Record<string, unknown>) => {
    const { watchDirectory } = require("./node/watcher.js");
    const interval = Number(opts["interval"] ?? 30) * 1000;
    console.log(`Watching ${dir} (interval: ${interval / 1000}s)...`);

    const ac = new AbortController();
    process.on("SIGINT", () => ac.abort());
    process.on("SIGTERM", () => ac.abort());

    await watchDirectory(dir, {
      interval,
      signal: ac.signal,
      onFileChanged: (filePath: string) => {
        console.log(`\nFile changed: ${filePath}`);
        const data = readFile(filePath);
        const result = scanData(data);
        const findings = applyConfidenceDowngrade(result.findings, false);
        printFindings(findings, result.profile.rowCount, result.profile.columnCount);
      },
    });
  });

// --- mcp-serve ---
program
  .command("mcp-serve")
  .description("Start MCP server over stdio (JSON-RPC 2.0)")
  .action(async () => {
    // `startMcpServer` is a hand-rolled, zero-dependency JSON-RPC-over-stdio
    // loop -- it does NOT need @modelcontextprotocol/sdk. This command used to
    // print that it did and exit(1), so the surface was "shared" in name only.
    const { startMcpServer } = await import("./node/mcp/server.js");
    startMcpServer();
  });

// --- agent-serve (A2A) ---
program
  .command("agent-serve")
  .description("Start the A2A agent server")
  .option("-p, --port <port>", "port", "8100")
  .action(async (opts: { port: string }) => {
    const { runA2aServer } = await import("./node/a2a/server.js");
    runA2aServer(parseInt(opts.port, 10));
  });

// --- demo ---
program
  .command("demo")
  .description("Generate and scan demo data")
  .option("--no-tui", "CLI output")
  .action((_opts: Record<string, unknown>) => {
    const { TabularData } = require("./core/data.js");
    const rows = Array.from({ length: 200 }, (_, i) => ({
      id: i + 1,
      name: i % 50 === 0 ? `Person${i}123` : `Person_${i}`,
      email: i < 180 ? `user${i}@example.com` : "not-an-email",
      age: i < 195 ? 20 + (i % 60) : -5,
      status: ["active", "inactive", "pending"][i % 3],
      phone: i < 190 ? `(555) 123-${String(i).padStart(4, "0")}` : "invalid",
    }));
    const data = new TabularData(rows);
    const result = scanData(data);
    const findings = applyConfidenceDowngrade(result.findings, false);
    printFindings(findings, result.profile.rowCount, result.profile.columnCount);
  });

// --- list-domains ---
program
  .command("list-domains")
  .description("List available domain packs")
  .action(() => {
    const domains = listAvailableDomains();
    console.log("\nAvailable domain packs:");
    for (const d of domains) {
      console.log(`  - ${d}`);
    }
  });

// --- history ---
program
  .command("history [file]")
  .description("Show scan history — scores, grades, and trends over time")
  .option("-n, --last <n>", "Show last N scans")
  .option("--json", "Output as JSON")
  .action((file: string | undefined, opts: Record<string, unknown>) => {
    // recordScan stores the resolved absolute path, so filter by the same.
    const fileFilter = file ? resolve(file) : undefined;
    const lastN = opts["last"] !== undefined ? Number(opts["last"]) : undefined;
    const records = loadHistory(fileFilter, lastN);

    if (records.length === 0) {
      console.log("No scan history found. Run a scan first.");
      return;
    }

    if (opts["json"]) {
      console.log(JSON.stringify(records, null, 2));
      return;
    }

    console.log(
      `${"Date".padEnd(20)} ${"File".padEnd(24)} ${"Score".padStart(5)} ` +
        `${"Grade".padStart(5)} ${"Errors".padStart(6)} ${"Warnings".padStart(8)}`,
    );
    for (const r of records) {
      const ts = r.timestamp.slice(0, 16).replace("T", " ");
      console.log(
        `${ts.padEnd(20)} ${r.file.padEnd(24)} ${String(r.score).padStart(5)} ` +
          `${r.grade.padStart(5)} ${String(r.errors).padStart(6)} ${String(r.warnings).padStart(8)}`,
      );
    }

    const first = records[0]!;
    const latest = records[records.length - 1]!;
    if (records.length >= 2 && first.file === latest.file) {
      console.log(`\nTrend: ${first.file} ${first.score} -> ${latest.score}`);
    }
  });

// --- evaluate ---
program
  .command("evaluate <file>")
  .description("Evaluate scan accuracy against a ground-truth JSON file")
  .requiredOption(
    "-g, --ground-truth <path>",
    "JSON file with expected findings (array of {column, check})",
  )
  .option("--min-f1 <n>", "Minimum F1 score; exit 1 if below", "0")
  .option("--json", "Output results as JSON")
  .action((file: string, opts: Record<string, unknown>) => {
    const gtPath = opts["groundTruth"] as string;
    let expected: ExpectedFinding[];
    try {
      expected = JSON.parse(readFileSync(gtPath, "utf-8")) as ExpectedFinding[];
    } catch (e) {
      console.error(
        `Error: could not read ground-truth file '${gtPath}': ${(e as Error).message}`,
      );
      process.exit(1);
    }

    const data = readFile(file);
    const result = scanData(data);
    const findings = applyConfidenceDowngrade(result.findings, false);
    const ev = evaluateScan(findings, expected);

    if (opts["json"]) {
      console.log(JSON.stringify(ev, null, 2));
    } else {
      console.log(`Precision:       ${ev.precision.toFixed(4)}`);
      console.log(`Recall:          ${ev.recall.toFixed(4)}`);
      console.log(`F1:              ${ev.f1.toFixed(4)}`);
      console.log(`True positives:  ${ev.truePositives}`);
      console.log(`False positives: ${ev.falsePositives}`);
      console.log(`False negatives: ${ev.falseNegatives}`);

      if (ev.fpDetails.length > 0) {
        console.log("\nFalse positives:");
        for (const [col, chk] of ev.fpDetails) console.log(`  ${col}: ${chk}`);
      }
      if (ev.fnDetails.length > 0) {
        console.log("\nFalse negatives (missed):");
        for (const [col, chk] of ev.fnDetails) console.log(`  ${col}: ${chk}`);
      }
    }

    const minF1 = Number(opts["minF1"] ?? 0);
    if (ev.f1 < minF1) {
      console.error(`\nF1 ${ev.f1.toFixed(4)} is below minimum ${minF1.toFixed(4)}`);
      process.exit(1);
    }
  });

// --- Helpers ---

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

function printFindings(findings: readonly Finding[], rows: number, cols: number): void {
  const byCol = findingsByColumn(findings);
  const { grade, points } = healthScore(byCol);
  const errors = findings.filter((f) => f.severity === Severity.ERROR).length;
  const warnings = findings.filter((f) => f.severity === Severity.WARNING).length;
  const infos = findings.filter((f) => f.severity === Severity.INFO).length;

  console.log(`\n${rows.toLocaleString()} rows, ${cols} columns — ${grade} (${points}/100)`);
  console.log(`${errors} error(s), ${warnings} warning(s), ${infos} info\n`);

  for (const f of findings) {
    const sev = f.severity === Severity.ERROR ? "ERROR  " : f.severity === Severity.WARNING ? "WARNING" : "INFO   ";
    const conf = f.confidence >= 0.8 ? "H" : f.confidence >= 0.5 ? "M" : "L";
    const src = f.source === "llm" ? " [LLM]" : f.source === "baseline_drift" ? " [DRIFT]" : "";
    console.log(`  ${sev} ${f.column.padEnd(20)} ${f.check.padEnd(22)} ${f.message}  (${conf}${src})`);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  program.parse();
}
