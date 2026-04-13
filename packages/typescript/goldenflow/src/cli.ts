#!/usr/bin/env node
/**
 * GoldenFlow JS CLI — Commander.js port of the Typer CLI.
 */

import { Command } from "commander";
import { readFile, writeFile } from "./node/connectors/file.js";
import { TransformEngine } from "./core/engine/transformer.js";
import { profileDataframe } from "./core/engine/profiler-bridge.js";
import { diffDataframes } from "./core/engine/differ.js";
import { learnConfig } from "./core/config/learner.js";
import { loadConfigFromString, saveConfigToString } from "./core/config/loader.js";
import { makeConfig } from "./core/types.js";
import { manifestToJson } from "./core/reporters/json-reporter.js";
import { SchemaMapper } from "./core/mapping/schema-mapper.js";
import { StreamProcessor } from "./core/engine/streaming.js";
import { listRuns } from "./node/history.js";

// Ensure all transforms are registered
import "./core/transforms/index.js";

const VERSION = "0.1.0";

const program = new Command()
  .name("goldenflow-js")
  .description("GoldenFlow: data transformation toolkit (TypeScript)")
  .version(VERSION);

program
  .command("transform <file>")
  .description("Transform a data file (zero-config or config-driven)")
  .option("-c, --config <path>", "YAML config file")
  .option("-o, --output-dir <dir>", "Output directory")
  .option("--domain <name>", "Domain pack to use")
  .option("--strict", "Fail if any transform errors occur")
  .option("--json", "Output manifest as JSON")
  .action(async (file: string, opts: Record<string, string | boolean | undefined>) => {
    const rows = readFile(file);
    let cfg = makeConfig();

    if (opts["config"]) {
      const { readFileSync } = await import("node:fs");
      const content = readFileSync(opts["config"] as string, "utf-8");
      cfg = loadConfigFromString(content);
    }

    if (opts["domain"]) {
      const { loadDomain } = await import("./core/domains/index.js");
      const pack = await loadDomain(opts["domain"] as string);
      if (pack) cfg = pack.defaultConfig;
    }

    const engine = new TransformEngine(cfg);
    const result = engine.transformDf(rows, file);

    if (opts["output-dir"]) {
      const dir = opts["output-dir"] as string;
      const { basename, extname: ext_, join } = await import("node:path");
      const { writeFileSync, mkdirSync } = await import("node:fs");
      mkdirSync(dir, { recursive: true });
      const ext = ext_(file);
      const stem = basename(file, ext);
      writeFile(result.rows as Record<string, unknown>[], join(dir, `${stem}_transformed${ext}`));
      writeFileSync(join(dir, `${stem}_manifest.json`), manifestToJson(result.manifest));
    }

    if (opts["json"]) {
      console.log(manifestToJson(result.manifest));
    } else {
      const m = result.manifest;
      console.log(`Transforms: ${m.records.length} | Errors: ${m.errors.length} | Rows: ${result.rows.length}`);
      for (const r of m.records.slice(0, 10)) {
        console.log(`  ${r.column}/${r.transform}: ${r.affectedRows}/${r.totalRows} affected`);
      }
    }

    if (opts["strict"] && result.manifest.errors.length > 0) {
      console.error(`Strict mode: ${result.manifest.errors.length} transform errors`);
      process.exit(1);
    }
  });

program
  .command("validate <file>")
  .description("Dry-run: show what would change")
  .option("-c, --config <path>", "YAML config file")
  .action(async (file: string, opts: Record<string, string | undefined>) => {
    const rows = readFile(file);
    let cfg = makeConfig();
    if (opts["config"]) {
      const { readFileSync } = await import("node:fs");
      cfg = loadConfigFromString(readFileSync(opts["config"] as string, "utf-8"));
    }
    const engine = new TransformEngine(cfg);
    const result = engine.transformDf(rows, file);
    console.log("Dry run — would change:");
    for (const r of result.manifest.records) {
      console.log(`  ${r.column}/${r.transform}: ${r.affectedRows} rows`);
    }
  });

program
  .command("profile <file>")
  .description("Show column profiles for a data file")
  .action((file: string) => {
    const rows = readFile(file);
    const prof = profileDataframe(rows, file);
    console.log(`${prof.rowCount} rows, ${prof.columnCount} columns\n`);
    for (const c of prof.columns) {
      const pct = (c.nullPct * 100).toFixed(0);
      console.log(`  ${c.name}: ${c.inferredType} | nulls: ${c.nullCount} (${pct}%) | unique: ${c.uniqueCount}`);
    }
  });

program
  .command("learn <file>")
  .description("Generate a YAML config from data patterns")
  .option("-o, --output <path>", "Output config path", "goldenflow.yaml")
  .action(async (file: string, opts: { output: string }) => {
    const rows = readFile(file);
    const cfg = learnConfig(rows, file);
    const yaml = saveConfigToString(cfg);
    const { writeFileSync } = await import("node:fs");
    writeFileSync(opts.output, yaml);
    console.log(`Config saved to ${opts.output}`);
  });

program
  .command("diff <before> <after>")
  .description("Compare pre/post transform files")
  .action((before: string, after: string) => {
    const bRows = readFile(before);
    const aRows = readFile(after);
    const result = diffDataframes(bRows, aRows);
    console.log(`Changes: ${result.totalChanges}`);
    console.log(`Rows: ${result.rowCountBefore} → ${result.rowCountAfter}`);
    if (result.addedColumns.length) console.log(`Added: ${result.addedColumns.join(", ")}`);
    if (result.removedColumns.length) console.log(`Removed: ${result.removedColumns.join(", ")}`);
    if (result.changedColumns.length) console.log(`Changed: ${result.changedColumns.join(", ")}`);
  });

program
  .command("map")
  .description("Auto-map schemas between source and target")
  .requiredOption("-s, --source <file>", "Source data file")
  .requiredOption("-t, --target <file>", "Target data file")
  .option("-o, --output <path>", "Save mapping config")
  .action(async (opts: { source: string; target: string; output?: string }) => {
    const sRows = readFile(opts.source);
    const tRows = readFile(opts.target);
    const mapper = new SchemaMapper();
    const mappings = mapper.map(sRows, tRows);
    for (const m of mappings) {
      const tier = m.confidence >= 0.9 ? "auto" : m.confidence >= 0.6 ? "suggest" : "skip";
      console.log(`  ${m.source} → ${m.target} (${m.confidence.toFixed(2)}) [${tier}]`);
    }
    if (opts.output) {
      const cfg = mapper.toConfig(mappings);
      const yaml = saveConfigToString(cfg);
      const { writeFileSync } = await import("node:fs");
      writeFileSync(opts.output, yaml);
      console.log(`\nMapping saved to ${opts.output}`);
    }
  });

program
  .command("stream <file>")
  .description("Stream-process a large file in chunks")
  .option("--chunk-size <n>", "Rows per batch", "10000")
  .option("-c, --config <path>", "YAML config file")
  .action(async (file: string, opts: Record<string, string | undefined>) => {
    const rows = readFile(file);
    let cfg = makeConfig();
    if (opts["config"]) {
      const { readFileSync } = await import("node:fs");
      cfg = loadConfigFromString(readFileSync(opts["config"] as string, "utf-8"));
    }
    const chunkSize = parseInt(opts["chunk-size"] ?? "10000", 10);
    const processor = new StreamProcessor(cfg);
    let totalRows = 0;
    for (const result of processor.streamRows(rows, chunkSize)) {
      totalRows += result.rows.length;
      console.log(`Batch ${processor.batchesProcessed}: ${result.rows.length} rows`);
    }
    console.log(`Streamed ${processor.batchesProcessed} batches, ${totalRows} rows total`);
  });

program
  .command("history")
  .description("Show recent transform runs")
  .option("-n, --limit <n>", "Number of runs", "20")
  .action((opts: { limit: string }) => {
    try {
      const runs = listRuns(parseInt(opts.limit, 10));
      if (runs.length === 0) {
        console.log("No transform history yet.");
        return;
      }
      for (const r of runs) {
        console.log(
          `  ${r.runId}  ${r.source}  rows=${r.rows}  transforms=${r.transformsApplied}  errors=${r.errors}  ${r.timestamp.slice(0, 19)}`,
        );
      }
    } catch {
      console.log("No transform history yet.");
    }
  });

program
  .command("demo")
  .description("Generate sample data for trying GoldenFlow")
  .option("-o, --output-dir <dir>", "Output directory", ".")
  .action(async (opts: { "output-dir": string }) => {
    const dir = opts["output-dir"];
    const { writeFileSync, mkdirSync } = await import("node:fs");
    const { join } = await import("node:path");
    mkdirSync(dir, { recursive: true });

    const demoData = [
      { name: "  John Smith  ", email: "JOHN@EXAMPLE.COM", phone: "(555) 123-4567", state: "Pennsylvania", signup_date: "03/15/2024", price: "$1,234.56", status: "active" },
      { name: "DR. JANE DOE", email: "  jane@test.com  ", phone: "555.987.6543", state: "CA", signup_date: "2024-01-20", price: "$99.99", status: "ACTIVE" },
      { name: "mcdonald, robert", email: "bob@test.com", phone: "+1-555-456-7890", state: "new york", signup_date: "Jan 5, 2023", price: "$0.50", status: "actve" },
      { name: "Mary O'Brien", email: "mary@sample.com", phone: "5554567890", state: "IL", signup_date: "12/25/2022", price: "$5,000.00", status: "inactive" },
    ];

    writeFile(demoData, join(dir, "demo_data.csv"));
    writeFileSync(
      join(dir, "demo_config.yaml"),
      `# GoldenFlow Demo Config
transforms:
  - column: name
    ops: [strip, title_case]
  - column: email
    ops: [lowercase, strip]
  - column: phone
    ops: [phone_e164]
  - column: state
    ops: [state_abbreviate]
  - column: signup_date
    ops: [date_iso8601]
  - column: price
    ops: [currency_strip]
`,
    );
    console.log("Demo files created:");
    console.log(`  Data:   ${join(dir, "demo_data.csv")}`);
    console.log(`  Config: ${join(dir, "demo_config.yaml")}`);
  });

program.parse();
