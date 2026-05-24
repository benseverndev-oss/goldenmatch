#!/usr/bin/env node
/**
 * GoldenPipe JS CLI — Commander.js port of the Typer CLI.
 * Commands: run, stages, validate, init.
 *
 * The FastAPI / A2A / MCP / TUI serve commands from the Python CLI are
 * deferred (see README + CLAUDE.md).
 */

import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { Command } from "commander";
import {
  Resolver,
  WiringError,
  buildDefaultRegistry,
  type PipeResult,
} from "./core/index.js";
import { run } from "./node/run.js";
import { loadConfig } from "./node/loadConfig.js";

const VERSION = "0.1.0";

function printResult(result: PipeResult, verbose: boolean): void {
  process.stdout.write(`GoldenPipe: ${result.source}\n`);
  process.stdout.write("Stage                     Status     Details\n");
  for (const [name, sr] of Object.entries(result.stages)) {
    const details = sr.error ?? "";
    process.stdout.write(`${name.padEnd(25)} ${sr.status.padEnd(10)} ${details}\n`);
  }
  process.stdout.write(
    `\n${result.status.toUpperCase()} | ${result.inputRows} rows | ${result.source}\n`,
  );

  if (result.errors.length > 0) {
    process.stdout.write("\nErrors:\n");
    for (const e of result.errors) process.stdout.write(`  - ${e}\n`);
  }

  if (verbose) {
    const reasoning = Object.entries(result.reasoning).filter(([k]) => !k.startsWith("_"));
    if (reasoning.length > 0) {
      process.stdout.write("\nReasoning:\n");
      for (const [k, v] of reasoning) process.stdout.write(`  ${k}: ${v}\n`);
    }
    const timing = Object.entries(result.timing);
    if (timing.length > 0) {
      process.stdout.write("\nTiming:\n");
      for (const [k, v] of timing) process.stdout.write(`  ${k}: ${v.toFixed(2)}s\n`);
    }
  }
}

const program = new Command()
  .name("goldenpipe-js")
  .description("GoldenPipe: Golden Suite orchestrator (TypeScript)")
  .version(VERSION);

program
  .command("run <source>")
  .description("Run a pipeline on a CSV data file")
  .option("-c, --config <path>", "Pipeline YAML config")
  .option("-v, --verbose", "Show reasoning and timing")
  .action(async (source: string, opts: { config?: string; verbose?: boolean }) => {
    const result = await run(source, opts.config !== undefined ? { config: opts.config } : {});
    printResult(result, opts.verbose ?? false);
    if (result.status === "failed") process.exitCode = 1;
  });

program
  .command("stages")
  .description("List all registered stages")
  .action(() => {
    const reg = buildDefaultRegistry();
    const all = reg.listAll();
    process.stdout.write("Registered stages\n");
    process.stdout.write("Name                       Produces             Consumes\n");
    for (const [name, info] of Object.entries(all).sort()) {
      process.stdout.write(
        `${name.padEnd(26)} ${info.produces.join(", ").padEnd(20)} ${info.consumes.join(", ")}\n`,
      );
    }
    process.stdout.write(`\n${Object.keys(all).length} stage(s) found\n`);
  });

program
  .command("validate")
  .description("Dry-run wiring validation without executing")
  .requiredOption("-c, --config <path>", "Pipeline YAML config")
  .action(async (opts: { config: string }) => {
    try {
      const cfg = await loadConfig(opts.config);
      const reg = buildDefaultRegistry();
      const plan = Resolver.resolve(cfg, reg);
      process.stdout.write(`Valid -- ${plan.stages.length} stages resolved\n`);
      for (const s of plan.stages) process.stdout.write(`  ${s.name}\n`);
    } catch (e) {
      if (e instanceof WiringError) {
        process.stderr.write(`Wiring Error: ${e.message}\n`);
      } else {
        const message = e instanceof Error ? e.message : String(e);
        process.stderr.write(`Error: ${message}\n`);
      }
      process.exitCode = 1;
    }
  });

program
  .command("init")
  .description("Generate a starter goldenpipe.yml from registered stages")
  .option("-d, --dir <dir>", "Directory to create config in", ".")
  .action((opts: { dir: string }) => {
    const reg = buildDefaultRegistry();
    const all = reg.listAll();
    const lines = ["pipeline: my-pipeline", "stages:"];
    // The `load` stage is auto-prepended by the resolver; don't list it.
    for (const name of Object.keys(all).sort()) {
      if (name === "load") continue;
      lines.push(`  - ${name}`);
    }
    const out = join(opts.dir, "goldenpipe.yml");
    writeFileSync(out, lines.join("\n") + "\n");
    process.stdout.write(`Created ${out}\n`);
  });

program.parseAsync(process.argv).catch((e: unknown) => {
  const message = e instanceof Error ? e.message : String(e);
  process.stderr.write(`${message}\n`);
  process.exitCode = 1;
});
