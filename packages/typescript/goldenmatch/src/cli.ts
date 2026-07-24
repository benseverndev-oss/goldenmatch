#!/usr/bin/env node
/**
 * cli.ts -- GoldenMatch command-line interface.
 *
 * Built on commander. Exposes `dedupe`, `match`, `score`, `profile`,
 * `info`, and `demo` subcommands.
 */

import { Command } from "commander";
import { extname, basename, dirname } from "node:path";
import { pathToFileURL } from "node:url";
import { randomUUID } from "node:crypto";
import {
  readFile,
  writeCsv,
  writeJson,
} from "./node/connectors/file.js";
import { dedupe, match, scoreStrings } from "./core/api.js";
import {
  buildLineage,
  evaluateClusters,
  explainCluster,
  explainPair,
  loadGroundTruthPairs,
} from "./core/index.js";
import { analyzeBlocking } from "./core/block-analyzer.js";
import { autoConfigure } from "./core/autoconfig.js";
import { loadConfigFile } from "./node/config-file.js";
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import {
  compareClusters,
  ccmsSummary,
  parseClustersJson,
} from "./core/compare-clusters.js";
import { runIncremental } from "./core/incremental.js";
import { emitHealerSurface } from "./node/cli-healer.js";
import type { ClusterInfo, PairKey, Row } from "./core/types.js";
import pkg from "../package.json" with { type: "json" };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function parseFuzzyArg(raw: string): Record<string, number> {
  const out: Record<string, number> = {};
  for (const pair of raw.split(",")) {
    const trimmed = pair.trim();
    if (trimmed === "") continue;
    const idx = trimmed.indexOf(":");
    let field: string;
    let threshold = 0.85;
    if (idx === -1) {
      field = trimmed;
    } else {
      field = trimmed.slice(0, idx).trim();
      const rawThreshold = trimmed.slice(idx + 1).trim();
      const parsed = parseFloat(rawThreshold);
      if (Number.isFinite(parsed)) threshold = parsed;
    }
    if (field !== "") out[field] = threshold;
  }
  return out;
}

function parseCsvList(raw: string): string[] {
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

function loadFilesWithSource(paths: readonly string[]): Row[] {
  const rows: Row[] = [];
  for (let i = 0; i < paths.length; i++) {
    const p = paths[i]!;
    const source = basename(p, extname(p)) || `file_${i}`;
    const fileRows = readFile(p);
    for (const r of fileRows) {
      rows.push({ ...r, __source__: source });
    }
  }
  return rows;
}

interface SharedMatchOpts {
  config?: string;
  exact?: string;
  fuzzy?: string;
  blocking?: string;
  threshold?: number;
  output?: string;
  format?: string;
}

interface DedupeCmdOpts extends SharedMatchOpts {
  suggest?: boolean;
  heal?: boolean;
}

function buildOptionsFromFlags(opts: SharedMatchOpts) {
  const out: {
    config?: ReturnType<typeof loadConfigFile>;
    exact?: string[];
    fuzzy?: Record<string, number>;
    blocking?: string[];
    threshold?: number;
  } = {};
  if (opts.config) out.config = loadConfigFile(opts.config);
  if (opts.exact) out.exact = parseCsvList(opts.exact);
  if (opts.fuzzy) out.fuzzy = parseFuzzyArg(opts.fuzzy);
  if (opts.blocking) out.blocking = parseCsvList(opts.blocking);
  if (opts.threshold !== undefined) out.threshold = opts.threshold;
  return out;
}

function writeOutputRows(
  path: string,
  rows: readonly Row[],
  format: string,
): void {
  const ext = extname(path).toLowerCase();
  const useJson =
    format === "json" ||
    ext === ".json" ||
    ext === ".jsonl" ||
    ext === ".ndjson";
  if (useJson) {
    writeJson(path, rows);
  } else {
    const delimiter = ext === ".tsv" ? "\t" : ",";
    writeCsv(path, rows, { delimiter });
  }
}

// ---------------------------------------------------------------------------
// CLI definition
// ---------------------------------------------------------------------------

export const program = new Command();

program
  .name("goldenmatch-js")
  .description("Entity resolution toolkit -- dedupe, match, build golden records")
  .version(pkg.version);

// ---------- dedupe ----------
program
  .command("dedupe")
  .description("Deduplicate records in one or more files")
  .argument("<files...>", "input file paths (.csv, .tsv, .json, .jsonl)")
  .option("-c, --config <path>", "path to YAML config file")
  .option("-e, --exact <fields>", "comma-separated exact match fields")
  .option(
    "-f, --fuzzy <fields>",
    "fuzzy match fields, e.g. 'name:0.85,email:0.9'",
  )
  .option("-b, --blocking <fields>", "comma-separated blocking keys")
  .option("-t, --threshold <value>", "overall fuzzy threshold", parseFloat)
  .option("-o, --output <path>", "output path for golden records")
  .option("--format <format>", "output format: csv or json", "csv")
  .option(
    "--suggest",
    "surface verified config-improvement suggestions (the healer; opt-in)",
  )
  .option(
    "--heal",
    "auto-apply the healer loop and run with the healed config",
  )
  .action(async (files: string[], opts: DedupeCmdOpts) => {
    const rows = loadFilesWithSource(files);
    const options = buildOptionsFromFlags(opts);
    const result = await dedupe(rows, {
      ...options,
      ...(opts.suggest ? { suggest: true } : {}),
      ...(opts.heal ? { heal: true } : {}),
    });
    const pct = (result.stats.matchRate * 100).toFixed(1);
    process.stdout.write(
      `Dedupe complete: ${result.stats.totalRecords} records -> ${result.stats.totalClusters} clusters (${pct}% match rate)\n`,
    );
    if (opts.output) {
      writeOutputRows(
        opts.output,
        result.goldenRecords,
        opts.format ?? "csv",
      );
      process.stdout.write(
        `Wrote ${result.goldenRecords.length} golden records to ${opts.output}\n`,
      );
    }
    // Healer surface: --suggest / --heal print their output; the default run
    // prints a free headroom hint to stderr, read off the result already
    // produced above (no second dedupe).
    emitHealerSurface(
      result,
      { suggest: opts.suggest === true, heal: opts.heal === true },
      {
        out: (s) => process.stdout.write(s),
        err: (s) => process.stderr.write(s),
      },
    );
  });

// ---------- match ----------
program
  .command("match")
  .description("Match target records against a reference dataset")
  .argument("<target>", "target file path")
  .argument("<reference>", "reference file path")
  .option("-c, --config <path>", "path to YAML config file")
  .option("-e, --exact <fields>", "comma-separated exact match fields")
  .option(
    "-f, --fuzzy <fields>",
    "fuzzy match fields, e.g. 'name:0.85,email:0.9'",
  )
  .option("-b, --blocking <fields>", "comma-separated blocking keys")
  .option("-t, --threshold <value>", "overall fuzzy threshold", parseFloat)
  .option("-o, --output <path>", "output path for matched records")
  .option("--format <format>", "output format: csv or json", "csv")
  .action(
    async (targetPath: string, referencePath: string, opts: SharedMatchOpts) => {
      const targetRows = readFile(targetPath).map((row) => ({
        ...row,
        __source__: "target",
      }));
      const referenceRows = readFile(referencePath).map((row) => ({
        ...row,
        __source__: "reference",
      }));
      const options = buildOptionsFromFlags(opts);
      const result = await match(targetRows, referenceRows, options);
      process.stdout.write(
        `Match complete: ${result.matched.length} matched, ${result.unmatched.length} unmatched\n`,
      );
      if (opts.output) {
        writeOutputRows(
          opts.output,
          result.matched,
          opts.format ?? "csv",
        );
        process.stdout.write(
          `Wrote ${result.matched.length} matched records to ${opts.output}\n`,
        );
      }
    },
  );

// ---------- score ----------
program
  .command("score")
  .description("Score similarity between two strings")
  .argument("<a>", "first string")
  .argument("<b>", "second string")
  .option(
    "-s, --scorer <name>",
    "scorer: exact, jaro_winkler, levenshtein, token_sort, soundex_match, dice, jaccard, ensemble",
    "jaro_winkler",
  )
  .action((a: string, b: string, opts: { scorer: string }) => {
    const score = scoreStrings(a, b, opts.scorer);
    process.stdout.write(`${opts.scorer}: ${score.toFixed(4)}\n`);
  });

// ---------- info ----------
program
  .command("info")
  .description("Show information about the package")
  .action(() => {
    process.stdout.write(`GoldenMatch JS v${pkg.version}\n`);
    process.stdout.write(
      "Scorers: exact, jaro_winkler, levenshtein, token_sort, soundex_match, dice, jaccard, ensemble\n",
    );
    process.stdout.write(
      "Strategies: most_complete, majority_vote, source_priority, most_recent, first_non_null\n",
    );
    process.stdout.write(
      "Blocking: static, multi_pass, sorted_neighborhood, adaptive\n",
    );
    process.stdout.write(
      "Transforms: lowercase, uppercase, strip, soundex, metaphone, digits_only, alpha_only, token_sort\n",
    );
  });

// ---------- evaluate ----------
interface EvaluateOpts extends SharedMatchOpts {
  groundTruth: string;
  colA: string;
  colB: string;
  minF1?: number;
}

program
  .command("evaluate")
  .description("Evaluate dedupe quality (precision/recall/F1) vs a ground-truth pairs file")
  .argument("<files...>", "input file paths")
  .requiredOption("--ground-truth <path>", "CSV of ground-truth match pairs")
  .option("-c, --config <path>", "path to YAML config file")
  .option("-e, --exact <fields>", "comma-separated exact match fields")
  .option("-f, --fuzzy <fields>", "fuzzy match fields, e.g. 'name:0.85'")
  .option("-b, --blocking <fields>", "comma-separated blocking keys")
  .option("-t, --threshold <value>", "overall fuzzy threshold", parseFloat)
  .option("--col-a <name>", "ground-truth column for id A", "id_a")
  .option("--col-b <name>", "ground-truth column for id B", "id_b")
  .option("--min-f1 <value>", "exit non-zero if F1 is below this (CI gate)", parseFloat)
  .action(async (files: string[], opts: EvaluateOpts) => {
    const rows = loadFilesWithSource(files);
    const result = await dedupe(rows, buildOptionsFromFlags(opts));
    const gt = loadGroundTruthPairs(readFile(opts.groundTruth), opts.colA, opts.colB);
    const allIds = rows.map((_, i) => i);
    const ev = evaluateClusters(result.clusters, gt, allIds);
    process.stdout.write(
      `Precision: ${ev.precision.toFixed(4)}  Recall: ${ev.recall.toFixed(4)}  F1: ${ev.f1.toFixed(4)}\n` +
        `TP: ${ev.truePositives}  FP: ${ev.falsePositives}  FN: ${ev.falseNegatives}\n`,
    );
    if (opts.minF1 !== undefined && ev.f1 < opts.minF1) {
      process.stderr.write(
        `F1 ${ev.f1.toFixed(4)} below --min-f1 ${opts.minF1}\n`,
      );
      process.exit(1);
    }
  });

// ---------- compare-clusters ----------
program
  .command("compare-clusters")
  .description("Compare two ER clustering outcomes using the CCMS framework")
  .argument("<file_a>", "first cluster JSON file (baseline / ER1)")
  .argument("<file_b>", "second cluster JSON file (comparison / ER2)")
  .option("-o, --output <path>", "save the CCMS summary to JSON")
  .action((fileA: string, fileB: string, opts: { output?: string }) => {
    const clustersA = parseClustersJson(JSON.parse(readFileSync(fileA, "utf-8")));
    const clustersB = parseClustersJson(JSON.parse(readFileSync(fileB, "utf-8")));
    const s = ccmsSummary(compareClusters(clustersA, clustersB));
    const pct = (x: number): string => `${(x * 100).toFixed(1)}%`;
    process.stdout.write(
      `CCMS Cluster Comparison\n` +
        `  Unchanged (UC):   ${s["unchanged"]} (${pct(s["unchanged_pct"]!)})\n` +
        `  Merged (MC):      ${s["merged"]} (${pct(s["merged_pct"]!)})\n` +
        `  Partitioned (PC): ${s["partitioned"]} (${pct(s["partitioned_pct"]!)})\n` +
        `  Overlapping (OC): ${s["overlapping"]} (${pct(s["overlapping_pct"]!)})\n` +
        `  Total References: ${s["rc"]}   ER1 clusters: ${s["cc1"]}   ER2 clusters: ${s["cc2"]}\n` +
        `  TWI: ${s["twi"]!.toFixed(4)}\n`,
    );
    if (opts.output) {
      writeFileSync(opts.output, JSON.stringify(s, null, 2) + "\n", "utf-8");
      process.stdout.write(`Summary saved to ${opts.output}\n`);
    }
  });

// ---------- incremental ----------
program
  .command("incremental")
  .description("Match new records against an existing base dataset incrementally")
  .argument("<base_file>", "base dataset file path")
  .requiredOption("-n, --new-records <path>", "new records CSV to match")
  .requiredOption("-c, --config <path>", "config YAML path")
  .option("-t, --threshold <value>", "override threshold", parseFloat)
  .option("-o, --output <path>", "output CSV path for the match pairs")
  .action(
    (
      baseFile: string,
      opts: { newRecords: string; config: string; threshold?: number; output?: string },
    ) => {
      const baseRows = readFile(baseFile);
      const newRows = readFile(opts.newRecords);
      const config = loadConfigFile(opts.config);
      const r = runIncremental(baseRows, newRows, config, opts.threshold);
      process.stdout.write(
        `Incremental Match Results\n` +
          `  New records processed: ${r.new_records}\n` +
          `  Matched to base:       ${r.matched_to_base}\n` +
          `  New entities:          ${r.new_entities}\n` +
          `  Total match pairs:     ${r.total_pairs}\n`,
      );
      if (opts.output && r.matches.length > 0) {
        writeCsv(opts.output, r.matches as unknown as Row[]);
        process.stdout.write(`Match pairs saved to ${opts.output}\n`);
      } else if (opts.output) {
        process.stdout.write("No matches found - no output written\n");
      }
    },
  );

// ---------- profile ----------
program
  .command("profile")
  .description("Profile a dataset (column stats, nulls, cardinality)")
  .argument("<file>", "input file")
  .action((file: string) => {
    const rows = readFile(file);
    const total = rows.length;
    process.stdout.write(`File: ${file}\n`);
    process.stdout.write(`Rows: ${total}\n`);
    if (total === 0) return;
    const columns = new Set<string>();
    for (const r of rows) for (const k of Object.keys(r)) columns.add(k);
    process.stdout.write(`Columns: ${columns.size}\n`);
    process.stdout.write("\n");
    const colList = [...columns];
    const nameWidth = Math.max(6, ...colList.map((c) => c.length));
    const pad = (s: string, w: number) => s + " ".repeat(Math.max(0, w - s.length));
    process.stdout.write(
      `${pad("column", nameWidth)}  ${pad("nulls", 8)}  ${pad("null%", 7)}  ${pad("distinct", 9)}  sample\n`,
    );
    process.stdout.write(
      `${"-".repeat(nameWidth)}  ${"-".repeat(8)}  ${"-".repeat(7)}  ${"-".repeat(9)}  ------\n`,
    );
    for (const col of colList) {
      let nulls = 0;
      const distinct = new Set<string>();
      let sample: string | null = null;
      for (const row of rows) {
        const v = row[col];
        if (v === null || v === undefined || v === "") {
          nulls++;
        } else {
          const s = String(v);
          distinct.add(s);
          if (sample === null) sample = s;
        }
      }
      const nullPct = ((nulls / total) * 100).toFixed(1);
      const sampleStr = sample === null ? "-" : sample.length > 30 ? sample.slice(0, 27) + "..." : sample;
      process.stdout.write(
        `${pad(col, nameWidth)}  ${pad(String(nulls), 8)}  ${pad(nullPct + "%", 7)}  ${pad(String(distinct.size), 9)}  ${sampleStr}\n`,
      );
    }
  });

// ---------- demo ----------
program
  .command("demo")
  .description("Run a quick demo on synthetic data")
  .action(async () => {
    const rows: Row[] = [
      { id: 1, name: "John Smith", email: "john@example.com", zip: "01234" },
      { id: 2, name: "Jon Smith", email: "john@example.com", zip: "01234" },
      { id: 3, name: "Jane Doe", email: "jane@example.com", zip: "02139" },
      { id: 4, name: "J. Doe", email: "jane@example.com", zip: "02139" },
      { id: 5, name: "Bob Jones", email: "bob@example.com", zip: "10001" },
    ];
    process.stdout.write(`Input: ${rows.length} synthetic records\n`);
    const result = await dedupe(rows, {
      exact: ["email"],
      fuzzy: { name: 0.8 },
      blocking: ["zip"],
      threshold: 0.8,
    });
    process.stdout.write(
      `Dedupe: ${result.stats.totalRecords} records -> ${result.stats.totalClusters} clusters\n`,
    );
    process.stdout.write(
      `Match rate: ${(result.stats.matchRate * 100).toFixed(1)}%\n`,
    );
    process.stdout.write(`Golden records: ${result.goldenRecords.length}\n`);
    for (const g of result.goldenRecords) {
      process.stdout.write(`  ${JSON.stringify(g)}\n`);
    }
  });

// ---------- memory ----------
const memoryCmd = program
  .command("memory")
  .description("Inspect and manage Learning Memory");

memoryCmd
  .command("stats")
  .description("Show counts, last learn time, and current adjustments")
  .option("--path <path>", "Memory DB path", ".goldenmatch/memory.db")
  .action(async (opts: { path: string }) => {
    const { memoryStats } = await import("./node/memory/api.js");
    const s = await memoryStats({ path: opts.path });
    process.stdout.write(`Corrections: ${s.count}\n`);
    process.stdout.write(
      `Last learn:  ${s.lastLearnTime ? s.lastLearnTime.toISOString() : "(never)"}\n`,
    );
    process.stdout.write(`Adjustments: ${s.adjustments.length}\n`);
    for (const a of s.adjustments) {
      const thr = a.threshold !== null ? a.threshold.toFixed(3) : "-";
      const learned = a.learnedAt.toISOString();
      process.stdout.write(
        `  ${a.matchkeyName}: threshold=${thr} samples=${a.sampleSize} learned=${learned}\n`,
      );
    }
  });

memoryCmd
  .command("learn")
  .description("Force a learning pass over stored corrections")
  .option("--matchkey-name <name>", "Limit learning to this matchkey")
  .option("--path <path>", "Memory DB path", ".goldenmatch/memory.db")
  .action(async (opts: { matchkeyName?: string; path: string }) => {
    const { learn } = await import("./node/memory/api.js");
    const learnOpts: { matchkeyName?: string; path?: string } = {
      path: opts.path,
    };
    if (opts.matchkeyName) learnOpts.matchkeyName = opts.matchkeyName;
    const adjustments = await learn(learnOpts);
    if (adjustments.length === 0) {
      process.stdout.write(
        "No adjustments produced (need >=10 corrections with both approve and reject decisions).\n",
      );
      return;
    }
    process.stdout.write(`Learned ${adjustments.length} adjustment(s):\n`);
    for (const a of adjustments) {
      const thr = a.threshold !== null ? a.threshold.toFixed(3) : "-";
      process.stdout.write(
        `  ${a.matchkeyName}: threshold=${thr} samples=${a.sampleSize}\n`,
      );
    }
  });

const _CSV_FIELDS = [
  "id",
  "id_a",
  "id_b",
  "decision",
  "source",
  "trust",
  "field_hash",
  "record_hash",
  "original_score",
  "matchkey_name",
  "reason",
  "dataset",
  "created_at",
] as const;

function csvEscape(v: string | number | null | undefined): string {
  if (v === null || v === undefined) return "";
  const s = String(v);
  if (s.includes(",") || s.includes('"') || s.includes("\n")) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

memoryCmd
  .command("export")
  .description("Dump all corrections as CSV")
  .argument("<out>", "Output CSV path")
  .option("--path <path>", "Memory DB path", ".goldenmatch/memory.db")
  .action(async (out: string, opts: { path: string }) => {
    const { writeFileSync, mkdirSync } = await import("node:fs");
    const { dirname } = await import("node:path");
    const { getMemory } = await import("./node/memory/api.js");
    const store = await getMemory({ path: opts.path });
    let corrections;
    try {
      corrections = await store.getCorrections();
    } finally {
      await store.close?.();
    }
    const dir = dirname(out);
    if (dir && dir !== ".") mkdirSync(dir, { recursive: true });
    const lines: string[] = [];
    lines.push(_CSV_FIELDS.join(","));
    for (const c of corrections) {
      lines.push(
        [
          csvEscape(c.id),
          csvEscape(c.idA),
          csvEscape(c.idB),
          csvEscape(c.decision),
          csvEscape(c.source),
          csvEscape(c.trust),
          csvEscape(c.fieldHash || ""),
          csvEscape(c.recordHash || ""),
          csvEscape(c.originalScore),
          csvEscape(c.matchkeyName || ""),
          csvEscape(c.reason || ""),
          csvEscape(c.dataset || ""),
          csvEscape(c.createdAt.toISOString()),
        ].join(","),
      );
    }
    writeFileSync(out, lines.join("\n") + "\n", "utf-8");
    process.stdout.write(`Exported ${corrections.length} corrections to ${out}\n`);
  });

function parseCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let i = 0;
  let inQuotes = false;
  while (i < line.length) {
    const ch = line[i]!;
    if (inQuotes) {
      if (ch === '"') {
        if (line[i + 1] === '"') {
          cur += '"';
          i += 2;
          continue;
        }
        inQuotes = false;
        i++;
        continue;
      }
      cur += ch;
      i++;
    } else {
      if (ch === '"') {
        inQuotes = true;
        i++;
      } else if (ch === ",") {
        out.push(cur);
        cur = "";
        i++;
      } else {
        cur += ch;
        i++;
      }
    }
  }
  out.push(cur);
  return out;
}

memoryCmd
  .command("import")
  .description("Load corrections from CSV. Skips malformed rows with a warning")
  .argument("<src>", "Source CSV path")
  .option("--path <path>", "Memory DB path", ".goldenmatch/memory.db")
  .action(async (src: string, opts: { path: string }) => {
    const { readFileSync, existsSync } = await import("node:fs");
    if (!existsSync(src)) {
      process.stderr.write(`File not found: ${src}\n`);
      process.exit(1);
    }
    const { getMemory } = await import("./node/memory/api.js");
    const { trustForSource } = await import("./core/memory/types.js");
    const text = readFileSync(src, "utf-8");
    const lines = text.split(/\r?\n/).filter((l) => l.length > 0);
    if (lines.length === 0) {
      process.stderr.write("Empty CSV file\n");
      process.exit(1);
    }
    const header = parseCsvLine(lines[0]!);
    const required = ["id_a", "id_b", "decision", "source"];
    const missing = required.filter((r) => !header.includes(r));
    if (missing.length > 0) {
      process.stderr.write(
        `Malformed CSV: missing required columns: ${JSON.stringify(missing)}\n`,
      );
      process.exit(1);
    }
    const colIdx: Record<string, number> = {};
    header.forEach((h, idx) => {
      colIdx[h] = idx;
    });
    const get = (cells: string[], col: string): string =>
      colIdx[col] !== undefined ? (cells[colIdx[col]!] ?? "") : "";

    const store = await getMemory({ path: opts.path });
    let imported = 0;
    let skipped = 0;
    try {
      for (let li = 1; li < lines.length; li++) {
        const cells = parseCsvLine(lines[li]!);
        const idA = parseInt(get(cells, "id_a"), 10);
        const idB = parseInt(get(cells, "id_b"), 10);
        if (!Number.isFinite(idA) || !Number.isFinite(idB)) {
          process.stderr.write(
            `Skipping malformed row ${li + 1}: cannot parse id_a/id_b\n`,
          );
          skipped++;
          continue;
        }
        const decision = get(cells, "decision");
        if (decision !== "approve" && decision !== "reject") {
          process.stderr.write(
            `Skipping malformed row ${li + 1}: invalid decision ${decision}\n`,
          );
          skipped++;
          continue;
        }
        const source = get(cells, "source") || "api";
        const trustRaw = get(cells, "trust");
        const trust = trustRaw ? parseFloat(trustRaw) : trustForSource(source);
        const origScoreRaw = get(cells, "original_score");
        const originalScore = origScoreRaw ? parseFloat(origScoreRaw) : 0.0;
        const createdRaw = get(cells, "created_at");
        let createdAt: Date;
        if (createdRaw) {
          const d = new Date(createdRaw);
          createdAt = isNaN(d.getTime()) ? new Date() : d;
        } else {
          createdAt = new Date();
        }
        const id = get(cells, "id") || crypto.randomUUID();
        await store.addCorrection({
          id,
          idA,
          idB,
          decision: decision as "approve" | "reject",
          source: source as
            | "steward"
            | "boost"
            | "unmerge"
            | "agent"
            | "llm"
            | "api",
          trust: Number.isFinite(trust) ? trust : 0.5,
          fieldHash: get(cells, "field_hash"),
          recordHash: get(cells, "record_hash"),
          originalScore: Number.isFinite(originalScore) ? originalScore : 0.0,
          matchkeyName: get(cells, "matchkey_name") || null,
          reason: get(cells, "reason") || null,
          dataset: get(cells, "dataset") || null,
          createdAt,
        });
        imported++;
      }
    } finally {
      await store.close?.();
    }
    let msg = `Imported ${imported} corrections from ${src}`;
    if (skipped > 0) msg += ` (skipped ${skipped} malformed row(s))`;
    process.stdout.write(msg + "\n");
  });

memoryCmd
  .command("show")
  .description("Pretty-print a single stored correction")
  .argument("<idA>", "First record ID")
  .argument("<idB>", "Second record ID")
  .option("--path <path>", "Memory DB path", ".goldenmatch/memory.db")
  .action(async (idAStr: string, idBStr: string, opts: { path: string }) => {
    const { getMemory } = await import("./node/memory/api.js");
    const idA = parseInt(idAStr, 10);
    const idB = parseInt(idBStr, 10);
    if (!Number.isFinite(idA) || !Number.isFinite(idB)) {
      process.stderr.write("idA and idB must be integers\n");
      process.exit(1);
    }
    const store = await getMemory({ path: opts.path });
    let c;
    try {
      // Try with no dataset first; if not found try without filter via getCorrections.
      c = await store.getCorrection(idA, idB, null);
      if (c === null) {
        const all = await store.getCorrections();
        const lo = Math.min(idA, idB);
        const hi = Math.max(idA, idB);
        c = all.find((x) => x.idA === lo && x.idB === hi) ?? null;
      }
    } finally {
      await store.close?.();
    }
    if (c === null) {
      process.stderr.write(`No correction found for pair (${idA}, ${idB}).\n`);
      process.exit(1);
    }
    process.stdout.write(`Correction (${c.idA}, ${c.idB}):\n`);
    process.stdout.write(`  id:             ${c.id}\n`);
    process.stdout.write(`  decision:       ${c.decision}\n`);
    process.stdout.write(`  source:         ${c.source}\n`);
    process.stdout.write(`  trust:          ${c.trust.toFixed(2)}\n`);
    process.stdout.write(`  matchkey_name:  ${c.matchkeyName ?? ""}\n`);
    process.stdout.write(`  reason:         ${c.reason ?? ""}\n`);
    process.stdout.write(`  dataset:        ${c.dataset ?? ""}\n`);
    process.stdout.write(
      `  original_score: ${c.originalScore.toFixed(3)}\n`,
    );
    process.stdout.write(`  field_hash:     ${c.fieldHash}\n`);
    process.stdout.write(`  record_hash:    ${c.recordHash}\n`);
    process.stdout.write(`  created_at:     ${c.createdAt.toISOString()}\n`);
  });

// ---------- identity ----------
// Mirrors `goldenmatch identity ...` in Python (cli/identity.py). Wraps the
// SqliteIdentityStore for inspecting nodes, source records, edges, events,
// and aliases. `resolve` is intentionally NOT yet shipped -- it depends on
// the pipeline-driven `resolveClusters` hook which is deferred to a future
// wave per CHANGELOG.md v0.10.0 deferred items.
const identityCmd = program
  .command("identity")
  .description("Inspect and manage the Identity Graph");

const DEFAULT_IDENTITY_PATH = ".goldenmatch/identity.db";

async function openIdentityStoreForCli(path: string) {
  const { existsSync } = await import("node:fs");
  if (!existsSync(path)) {
    process.stderr.write(`Identity DB not found: ${path}\n`);
    process.exit(2);
  }
  const { SqliteIdentityStore } = await import("./node/identity/sqlite-store.js");
  return SqliteIdentityStore.open({ path });
}

identityCmd
  .command("list")
  .description("List identities (most recently updated first)")
  .option("--path <path>", "Identity DB path", DEFAULT_IDENTITY_PATH)
  .option("--dataset <name>", "Filter by dataset")
  .option(
    "--status <status>",
    "Filter by status (active | merged_into | split | retired)",
  )
  .option("--limit <n>", "Max rows", "50")
  .option("--offset <n>", "Pagination offset", "0")
  .option("--json", "Emit JSON instead of a table")
  .action(
    async (opts: {
      path: string;
      dataset?: string;
      status?: string;
      limit: string;
      offset: string;
      json?: boolean;
    }) => {
      const store = await openIdentityStoreForCli(opts.path);
      try {
        const listOpts: {
          dataset?: string;
          status?: "active" | "merged_into" | "split" | "retired";
          limit?: number;
          offset?: number;
        } = {
          limit: Number(opts.limit),
          offset: Number(opts.offset),
        };
        if (opts.dataset) listOpts.dataset = opts.dataset;
        if (opts.status) {
          listOpts.status = opts.status as
            | "active"
            | "merged_into"
            | "split"
            | "retired";
        }
        const rows = await store.listIdentities(listOpts);
        if (opts.json) {
          process.stdout.write(JSON.stringify(rows, null, 2) + "\n");
          return;
        }
        process.stdout.write(`Identities (${rows.length})\n`);
        for (const r of rows) {
          const conf = r.confidence !== null ? r.confidence.toFixed(3) : "-";
          process.stdout.write(
            `  ${r.entityId.substring(0, 8)}...  ${r.status}  conf=${conf}  ` +
              `dataset=${r.dataset ?? "-"}  updated=${r.updatedAt.toISOString()}\n`,
          );
        }
      } finally {
        await store.close();
      }
    },
  );

identityCmd
  .command("show")
  .description("Show an identity with members, edges, and recent events")
  .argument("<entity-id>", "Entity ID")
  .option("--path <path>", "Identity DB path", DEFAULT_IDENTITY_PATH)
  .option("--json", "Emit JSON instead of a summary")
  .action(async (entityId: string, opts: { path: string; json?: boolean }) => {
    const store = await openIdentityStoreForCli(opts.path);
    try {
      const node = await store.getIdentity(entityId);
      if (!node) {
        process.stderr.write(`Not found: ${entityId}\n`);
        process.exit(1);
      }
      const records = await store.getRecordsForEntity(entityId);
      const edges = await store.edgesForEntity(entityId);
      const events = await store.history(entityId);
      if (opts.json) {
        process.stdout.write(
          JSON.stringify({ node, records, edges, events }, null, 2) + "\n",
        );
        return;
      }
      const conf = node?.confidence !== null ? String(node?.confidence) : "-";
      process.stdout.write(`${node?.entityId}  status=${node?.status}\n`);
      process.stdout.write(`  confidence: ${conf}\n`);
      process.stdout.write(`  dataset:    ${node?.dataset ?? "-"}\n`);
      process.stdout.write(
        `  records:    ${records.length}, edges: ${edges.length}, events: ${events.length}\n`,
      );
      for (const r of records) {
        process.stdout.write(
          `    - ${r.recordId}  source=${r.source}  hash=${r.recordHash.substring(0, 12)}\n`,
        );
      }
    } finally {
      await store.close();
    }
  });

identityCmd
  .command("history")
  .description("Show event log for an identity")
  .argument("<entity-id>", "Entity ID")
  .option("--path <path>", "Identity DB path", DEFAULT_IDENTITY_PATH)
  .option("--limit <n>", "Max events", "50")
  .option("--json", "Emit JSON")
  .action(
    async (
      entityId: string,
      opts: { path: string; limit: string; json?: boolean },
    ) => {
      const store = await openIdentityStoreForCli(opts.path);
      try {
        const events = await store.history(entityId, Number(opts.limit));
        if (opts.json) {
          process.stdout.write(JSON.stringify(events, null, 2) + "\n");
          return;
        }
        process.stdout.write(`Events (${events.length})\n`);
        for (const e of events) {
          process.stdout.write(
            `  ${e.recordedAt.toISOString()}  ${e.kind}  run=${e.runName ?? "-"}\n`,
          );
        }
      } finally {
        await store.close();
      }
    },
  );

identityCmd
  .command("conflicts")
  .description("List conflict edges (kind=conflicts_with)")
  .option("--path <path>", "Identity DB path", DEFAULT_IDENTITY_PATH)
  .option("--dataset <name>", "Filter by dataset")
  .option("--json", "Emit JSON")
  .action(
    async (opts: { path: string; dataset?: string; json?: boolean }) => {
      const store = await openIdentityStoreForCli(opts.path);
      try {
        const conflicts = await store.findConflicts(opts.dataset);
        if (opts.json) {
          process.stdout.write(JSON.stringify(conflicts, null, 2) + "\n");
          return;
        }
        process.stdout.write(`Conflicts (${conflicts.length})\n`);
        for (const c of conflicts) {
          process.stdout.write(
            `  ${c.recordedAt.toISOString()}  entity=${c.entityId.substring(0, 8)}` +
              `  pair=${c.recordAId},${c.recordBId}  score=${c.score ?? "-"}\n`,
          );
        }
      } finally {
        await store.close();
      }
    },
  );

identityCmd
  .command("merge")
  .description("Manually merge two identities (source -> target)")
  .argument("<source-id>", "Source entity ID")
  .argument("<target-id>", "Target entity ID")
  .option("--path <path>", "Identity DB path", DEFAULT_IDENTITY_PATH)
  .action(
    async (
      sourceId: string,
      targetId: string,
      opts: { path: string },
    ) => {
      const store = await openIdentityStoreForCli(opts.path);
      try {
        const { manualMerge } = await import("./core/identity/query.js");
        // manualMerge(store, keep, absorb) -- target stays, source is absorbed.
        const result = await manualMerge(store, targetId, sourceId);
        process.stdout.write(`Merged ${sourceId} -> ${targetId}\n`);
        process.stdout.write(JSON.stringify(result, null, 2) + "\n");
      } finally {
        await store.close();
      }
    },
  );

identityCmd
  .command("split")
  .description("Manually split records into a new identity")
  .argument("<entity-id>", "Source entity ID")
  .argument("<record-ids...>", "Record IDs to move into a new identity")
  .option("--path <path>", "Identity DB path", DEFAULT_IDENTITY_PATH)
  .action(
    async (
      entityId: string,
      recordIds: string[],
      opts: { path: string },
    ) => {
      const store = await openIdentityStoreForCli(opts.path);
      try {
        const { manualSplit } = await import("./core/identity/query.js");
        const result = await manualSplit(store, entityId, recordIds);
        process.stdout.write(
          `Split ${recordIds.length} record(s) from ${entityId}\n`,
        );
        process.stdout.write(JSON.stringify(result, null, 2) + "\n");
      } finally {
        await store.close();
      }
    },
  );

// ---------- import-splink ----------
program
  .command("import-splink")
  .description("Convert a Splink settings (or trained-model) JSON file into a GoldenMatch YAML config")
  .argument("<input>", "Splink settings or trained-model JSON file")
  .option("-o, --output <path>", "Output YAML config path", "goldenmatch.yaml")
  .option(
    "--model-out <path>",
    "Persist imported trained m/u as an FS model JSON; sets model_path in the config",
  )
  .option("--strict", "Fail on any lossy mapping (warnings), not just errors")
  .action(
    async (
      input: string,
      opts: { output: string; modelOut?: string; strict?: boolean },
    ) => {
      const { runImportSplinkCli } = await import("./node/cli-import-splink.js");
      const splinkOpts: { output: string; modelOut?: string; strict?: boolean } = {
        output: opts.output,
      };
      if (opts.modelOut !== undefined) splinkOpts.modelOut = opts.modelOut;
      if (opts.strict !== undefined) splinkOpts.strict = opts.strict;
      const code = runImportSplinkCli(input, splinkOpts, {
        out: (s: string) => process.stdout.write(s),
        err: (s: string) => process.stderr.write(s),
      });
      if (code !== 0) process.exit(code);
    },
  );

// ---------- mcp-serve ----------
program
  .command("mcp-serve")
  .description("Start MCP server over stdio (JSON-RPC 2.0)")
  .action(async () => {
    const { startMcpServer } = await import("./node/mcp/server.js");
    startMcpServer();
  });

// ---------- serve (REST API) ----------
program
  .command("serve")
  .description("Start the REST API server")
  .option("-p, --port <port>", "port", "8000")
  .option("-h, --host <host>", "host", "127.0.0.1")
  .action(async (opts: { port: string; host: string }) => {
    const { startApiServer } = await import("./node/api/server.js");
    startApiServer({ port: parseInt(opts.port, 10), host: opts.host });
  });

// ---------- agent-serve (A2A) ----------
program
  .command("agent-serve")
  .description("Start the A2A agent-to-agent server")
  .option("-p, --port <port>", "port", "8200")
  .option("-h, --host <host>", "host", "127.0.0.1")
  .action(async (opts: { port: string; host: string }) => {
    const { startA2aServer } = await import("./node/a2a/server.js");
    startA2aServer({ port: parseInt(opts.port, 10), host: opts.host });
  });

// ---------- tui / interactive (one command, two names) ----------
//
// Python's CLI calls this `interactive`; the TS CLI has always called it `tui`.
// They are the SAME operation (launch the TUI over optional input files), so the
// parity manifest was counting ONE capability as BOTH a python_only gap and a
// ts_only gap. Registering both names on both sides closes both halves.
//
// It must be a real second `.command()`, NOT `.alias("interactive")`: the API
// surface emitter reads `program.commands.map(c => c.name())`, which does not
// see commander aliases -- an alias would leave the manifest lying.
interface TuiCmdOpts {
  config?: string;
  memoryPath?: string;
  outputDir?: string;
}

async function runTuiCommand(files: string[], opts: TuiCmdOpts): Promise<void> {
  try {
    const { startTui } = await import("./node/tui/app.js");
    const tuiOpts: {
      files?: string[];
      config?: ReturnType<typeof loadConfigFile>;
      memoryPath?: string;
      outputDir?: string;
    } = {};
    if (files && files.length > 0) tuiOpts.files = files;
    if (opts.config) tuiOpts.config = loadConfigFile(opts.config);
    if (opts.memoryPath) tuiOpts.memoryPath = opts.memoryPath;
    if (opts.outputDir) tuiOpts.outputDir = opts.outputDir;
    await startTui(tuiOpts);
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    process.stderr.write(`TUI error: ${message}\n`);
    process.exit(1);
  }
}

for (const cmdName of ["tui", "interactive"] as const) {
  program
    .command(cmdName)
    .description("Launch the interactive TUI (requires optional peer deps: ink + react)")
    .argument("[files...]", "input files to load on startup")
    .option("-c, --config <path>", "path to YAML config file")
    .option("--memory-path <path>", "Learning Memory SQLite path for Boost-tab labels")
    .option("-o, --output-dir <dir>", "directory the Export tab writes into")
    .action(runTuiCommand);
}

// ---------- analyze-blocking ----------
program
  .command("analyze-blocking")
  .description("Analyze data and suggest optimal blocking strategies")
  .argument("<files...>", "input file paths (.csv, .tsv, .json, .jsonl)")
  .option("-c, --config <path>", "config file whose matchkey fields to block on")
  .option("--top <n>", "how many suggestions to show", (v) => parseInt(v, 10), 5)
  .option("-o, --output <path>", "save the full suggestion list to JSON")
  .action((files: string[], opts: { config?: string; top: number; output?: string }) => {
    const rows = loadFilesWithSource(files);
    // Block on the configured matchkey fields when a config is supplied;
    // otherwise fall back to the fields zero-config would pick.
    const cfg = opts.config ? loadConfigFile(opts.config) : autoConfigure(rows);
    const columns = [
      ...new Set(
        (cfg.matchkeys ?? []).flatMap((mk) =>
          (mk.fields ?? []).map((f) => f.field).filter((f): f is string => !!f),
        ),
      ),
    ];
    if (columns.length === 0) {
      process.stderr.write("No matchkey fields to analyze; pass --config.\n");
      process.exit(1);
    }
    const suggestions = analyzeBlocking(rows, columns);
    process.stdout.write(
      `Blocking analysis: ${rows.length} records, ${columns.length} candidate field(s)\n`,
    );
    for (const s of suggestions.slice(0, opts.top)) {
      process.stdout.write(
        `  ${s.description}\n` +
          `      groups=${s.group_count} max=${s.max_group_size} ` +
          `mean=${s.mean_group_size.toFixed(1)} comparisons=${s.total_comparisons} ` +
          `recall~${(s.estimated_recall * 100).toFixed(1)}% score=${s.score.toFixed(3)}\n`,
      );
    }
    if (opts.output) {
      writeFileSync(opts.output, JSON.stringify(suggestions, null, 2) + "\n", "utf-8");
      process.stdout.write(`Suggestions saved to ${opts.output}\n`);
    }
  });

// ---------- autoconfig ----------
program
  .command("autoconfig")
  .description("Derive a config from the data (zero-config) and print it")
  .argument("<files...>", "input file paths (.csv, .tsv, .json, .jsonl)")
  .option("-o, --output <path>", "save the derived config to JSON")
  .action((files: string[], opts: { output?: string }) => {
    const rows = loadFilesWithSource(files);
    const cfg = autoConfigure(rows);
    const mks = cfg.matchkeys ?? [];
    process.stdout.write(
      `Auto-config derived from ${rows.length} records: ${mks.length} matchkey(s)\n`,
    );
    for (const mk of mks) {
      const fields = (mk.fields ?? [])
        .map((f) => `${f.field}${f.scorer ? `:${f.scorer}` : ""}`)
        .join(", ");
      process.stdout.write(
        `  ${mk.name ?? "(unnamed)"} [${mk.type}]` +
          `${mk.threshold != null ? ` threshold=${mk.threshold}` : ""}\n` +
          `      fields: ${fields || "(none)"}\n`,
      );
    }
    if (cfg.blocking) {
      process.stdout.write(
        `  blocking: ${cfg.blocking.strategy ?? "static"}` +
          `${cfg.blocking.keys ? ` on ${cfg.blocking.keys.join(", ")}` : ""}\n`,
      );
    }
    if (opts.output) {
      writeFileSync(opts.output, JSON.stringify(cfg, null, 2) + "\n", "utf-8");
      process.stdout.write(`Config saved to ${opts.output}\n`);
    }
  });

// ---------- lineage ----------
program
  .command("lineage")
  .description("Build + persist the per-pair match lineage for a run")
  .argument("<files...>", "input file paths (.csv, .tsv, .json, .jsonl)")
  .option("-c, --config <path>", "path to YAML config file")
  .option("-e, --exact <fields>", "comma-separated exact match fields")
  .option("-f, --fuzzy <fields>", "fuzzy match fields, e.g. 'name:0.85'")
  .option("-b, --blocking <fields>", "comma-separated blocking keys")
  .option("-t, --threshold <value>", "overall fuzzy threshold", parseFloat)
  .option("-o, --output <path>", "lineage JSON output path", "lineage.json")
  .action(async (files: string[], opts: SharedMatchOpts & { output: string }) => {
    const rows = loadFilesWithSource(files);
    const result = await dedupe(rows, buildOptionsFromFlags(opts));
    const bundle = buildLineage(result);
    writeFileSync(opts.output, JSON.stringify(bundle, null, 2) + "\n", "utf-8");
    // NOTE: bundle.recordCount is edges.length (a misnomer in core/lineage.ts),
    // so report the pair/edge count and the run's record count separately rather
    // than echoing it as a record total.
    process.stdout.write(
      `Lineage: ${bundle.edges.length} pair edge(s) over ` +
        `${result.stats.totalRecords} record(s) -> ${opts.output}\n`,
    );
  });

// ---------- explain ----------
program
  .command("explain")
  .description("Explain why a pair matched, or summarize a cluster")
  .argument("<files...>", "input file paths (.csv, .tsv, .json, .jsonl)")
  .option("-c, --config <path>", "path to YAML config file")
  .option("-e, --exact <fields>", "comma-separated exact match fields")
  .option("-f, --fuzzy <fields>", "fuzzy match fields, e.g. 'name:0.85'")
  .option("-b, --blocking <fields>", "comma-separated blocking keys")
  .option("-t, --threshold <value>", "overall fuzzy threshold", parseFloat)
  .option("--pair <a,b>", "explain a record pair by row id, e.g. '0,1'")
  .option("--cluster <id>", "explain a cluster by id", (v) => parseInt(v, 10))
  .action(
    async (
      files: string[],
      opts: SharedMatchOpts & { pair?: string; cluster?: number },
    ) => {
      if (!opts.pair && opts.cluster == null) {
        process.stderr.write("Pass --pair <a,b> or --cluster <id>.\n");
        process.exit(1);
      }
      const rows = loadFilesWithSource(files);
      const result = await dedupe(rows, buildOptionsFromFlags(opts));
      const mk = (result.config.matchkeys ?? [])[0];
      if (!mk) {
        process.stderr.write("No matchkey in the resolved config; nothing to explain.\n");
        process.exit(1);
      }
      if (opts.pair) {
        const [a, b] = opts.pair.split(",").map((s) => parseInt(s.trim(), 10));
        const rowA = rows[a as number];
        const rowB = rows[b as number];
        if (!rowA || !rowB) {
          process.stderr.write(`Row id out of range (have ${rows.length} rows).\n`);
          process.exit(1);
        }
        const ex = explainPair(rowA, rowB, mk);
        process.stdout.write(
          `${ex.explanation}\n` +
            `  score=${ex.score.toFixed(4)} confidence=${ex.confidence}\n` +
            ex.reasoning.map((r) => `  - ${r}\n`).join(""),
        );
      } else {
        const cluster = result.clusters.get(opts.cluster as number);
        if (!cluster) {
          process.stderr.write(`Cluster ${opts.cluster} not found.\n`);
          process.exit(1);
        }
        process.stdout.write(
          explainCluster(opts.cluster as number, cluster, rows, mk).summary + "\n",
        );
      }
    },
  );

// ---------- runs ----------
program
  .command("runs")
  .description("List previous runs (for rollback)")
  .option("--output-dir <dir>", "directory containing the run log", ".")
  .action(async (opts: { outputDir: string }) => {
    const { listRuns } = await import("./node/mcp/run-log.js");
    const runs = listRuns(opts.outputDir);
    if (runs.length === 0) {
      process.stdout.write("No runs recorded.\n");
      return;
    }
    process.stdout.write(`${runs.length} run(s):\n`);
    for (const r of runs) {
      const state = r.rolled_back ? "rolled back" : "active";
      process.stdout.write(
        `  ${r.run_id}  ${r.timestamp}  ${r.output_files.length} file(s)  [${state}]\n`,
      );
    }
  });

// ---------- rollback ----------
program
  .command("rollback")
  .description("Roll back a previous run by deleting its output files")
  .argument("<run_id>", "run id to roll back")
  .option("--output-dir <dir>", "directory containing the run log", ".")
  .action(async (runId: string, opts: { outputDir: string }) => {
    const { rollbackRun } = await import("./node/mcp/run-log.js");
    const res = rollbackRun(runId, opts.outputDir);
    if ("error" in res) {
      process.stderr.write(`Error: ${res.error}\n`);
      if (res.available_runs?.length) {
        process.stderr.write(`Available runs: ${res.available_runs.join(", ")}\n`);
      }
      process.exit(1);
    }
    process.stdout.write(`Rolled back ${res.run_id}\n`);
    for (const f of res.deleted) process.stdout.write(`  deleted: ${f}\n`);
    for (const f of res.not_found) process.stdout.write(`  missing: ${f}\n`);
  });

// ---------- unmerge ----------
//
// Operates on EXPORTED FILES, not a live run (same as Python): a clusters CSV
// carrying `__row_id__` + `__cluster_id__`, plus an optional scored-pairs CSV
// (`id_a,id_b,score`) supplying the edge weights that re-clustering needs --
// a clusters CSV alone has no pair scores to re-cluster from.
program
  .command("unmerge")
  .description("Remove a record from its cluster (per-entity unmerge)")
  .argument("<record_id>", "record row id to unmerge", (v) => parseInt(v, 10))
  .option("--clusters <path>", "clusters CSV from a previous run (required)")
  .option("--pairs <path>", "scored-pairs CSV (id_a,id_b,score)")
  .option("--shatter", "shatter the whole cluster into singletons")
  .option("-t, --threshold <value>", "min score for re-clustering", parseFloat, 0)
  .option("-o, --output <path>", "output CSV (default: <clusters>.unmerged.csv)")
  .action(
    async (
      recordId: number,
      opts: {
        clusters?: string;
        pairs?: string;
        shatter?: boolean;
        threshold: number;
        output?: string;
      },
    ) => {
      if (!opts.clusters) {
        process.stderr.write(
          "Error: --clusters is required.\n" +
            "Generate a clusters CSV with: goldenmatch dedupe --output-clusters\n",
        );
        process.exit(2);
      }
      const { unmergeRecord, unmergeCluster, pairKey } = await import("./core/cluster.js");
      const rows = readFile(opts.clusters);
      const first = rows[0];
      if (!first || !("__row_id__" in first) || !("__cluster_id__" in first)) {
        process.stderr.write(
          "Error: clusters file must contain __row_id__ and __cluster_id__ columns.\n",
        );
        process.exit(2);
      }

      // rows -> Map<cluster_id, members[]>
      const members = new Map<number, number[]>();
      const rowCid = new Map<number, number>();
      for (const r of rows) {
        const rid = Number(r["__row_id__"]);
        const cid = Number(r["__cluster_id__"]);
        if (!Number.isFinite(rid) || !Number.isFinite(cid)) continue;
        rowCid.set(rid, cid);
        const list = members.get(cid);
        if (list) list.push(rid);
        else members.set(cid, [rid]);
      }
      const targetCid = rowCid.get(recordId);
      if (targetCid === undefined) {
        process.stderr.write(`Record ${recordId} not found in clusters file.\n`);
        process.exit(1);
      }

      // Optional scored pairs -> per-cluster pairScores (edge weights).
      const pairScoresFor = new Map<number, Map<PairKey, number>>();
      if (opts.pairs) {
        for (const p of readFile(opts.pairs)) {
          const a = Number(p["id_a"]);
          const b = Number(p["id_b"]);
          const s = Number(p["score"]);
          if (!Number.isFinite(a) || !Number.isFinite(b) || !Number.isFinite(s)) continue;
          const cid = rowCid.get(a);
          if (cid === undefined || cid !== rowCid.get(b)) continue; // intra-cluster only
          let m = pairScoresFor.get(cid);
          if (!m) {
            m = new Map<PairKey, number>();
            pairScoresFor.set(cid, m);
          }
          m.set(pairKey(a, b), s);
        }
      }

      let clusters: Map<number, ClusterInfo> = new Map(
        [...members].map(([cid, mem]): [number, ClusterInfo] => [
          cid,
          {
            members: mem,
            size: mem.length,
            oversized: false,
            pairScores: pairScoresFor.get(cid) ?? new Map<PairKey, number>(),
            confidence: 1,
            bottleneckPair: null,
            clusterQuality: "strong",
          },
        ]),
      );

      const before = clusters.get(targetCid)?.members.length ?? 0;
      process.stdout.write(`Unmerge record ${recordId}\n`);
      process.stdout.write(`  Found in cluster ${targetCid} (${before} members)\n`);

      if (opts.shatter) {
        process.stdout.write(`  Shattering cluster ${targetCid} into ${before} singletons\n`);
        clusters = await unmergeCluster(targetCid, clusters);
      } else {
        process.stdout.write(`  Removing record ${recordId} from cluster\n`);
        clusters = await unmergeRecord(recordId, clusters, opts.threshold);
      }

      // Re-assign cluster ids onto the source rows and write out.
      const newCid = new Map<number, number>();
      for (const [cid, info] of clusters) for (const m of info.members) newCid.set(m, cid);
      const updated = rows.map((r) => ({
        ...r,
        __cluster_id__: newCid.get(Number(r["__row_id__"])) ?? null,
      }));
      const out = opts.output ?? `${opts.clusters}.unmerged.csv`;
      writeOutputRows(out, updated as Row[], "csv");
      process.stdout.write(`  ${clusters.size} cluster(s) after unmerge -> ${out}\n`);
    },
  );

// ---------- config (preset sub-app) ----------
//
// Mirrors Python's `config` Typer sub-app over the same
// `~/.goldenmatch/presets/<name>.yaml` layout, so presets are interchangeable.
const configCmd = program
  .command("config")
  .description("Manage saved config presets");

configCmd
  .command("save")
  .description("Save a config file as a named preset")
  .argument("<name>", "preset name")
  .argument("<config_path>", "path to the config YAML")
  .action(async (name: string, configPath: string) => {
    const { PresetStore } = await import("./node/preset-store.js");
    try {
      const dest = new PresetStore().save(name, configPath);
      process.stdout.write(`Preset '${name}' saved to ${dest}\n`);
    } catch (err: unknown) {
      process.stderr.write(`Error: ${err instanceof Error ? err.message : String(err)}\n`);
      process.exit(1);
    }
  });

configCmd
  .command("load")
  .description("Load a named preset to a local file")
  .argument("<name>", "preset name")
  .option("-d, --dest <path>", "destination path", "goldenmatch.yaml")
  .action(async (name: string, opts: { dest: string }) => {
    const { PresetStore } = await import("./node/preset-store.js");
    try {
      const out = new PresetStore().load(name, opts.dest);
      process.stdout.write(`Preset '${name}' written to ${out}\n`);
    } catch (err: unknown) {
      process.stderr.write(`Error: ${err instanceof Error ? err.message : String(err)}\n`);
      process.exit(1);
    }
  });

configCmd
  .command("list")
  .description("List saved presets")
  .action(async () => {
    const { PresetStore } = await import("./node/preset-store.js");
    const names = new PresetStore().listPresets();
    if (names.length === 0) {
      process.stdout.write("No presets saved.\n");
      return;
    }
    for (const n of names) process.stdout.write(`  ${n}\n`);
  });

configCmd
  .command("delete")
  .description("Delete a saved preset")
  .argument("<name>", "preset name")
  .action(async (name: string) => {
    const { PresetStore } = await import("./node/preset-store.js");
    try {
      new PresetStore().delete(name);
      process.stdout.write(`Preset '${name}' deleted\n`);
    } catch (err: unknown) {
      process.stderr.write(`Error: ${err instanceof Error ? err.message : String(err)}\n`);
      process.exit(1);
    }
  });

configCmd
  .command("show")
  .description("Print a saved preset")
  .argument("<name>", "preset name")
  .action(async (name: string) => {
    const { PresetStore } = await import("./node/preset-store.js");
    try {
      process.stdout.write(new PresetStore().show(name));
    } catch (err: unknown) {
      process.stderr.write(`Error: ${err instanceof Error ? err.message : String(err)}\n`);
      process.exit(1);
    }
  });

// ---------- schedule ----------
program
  .command("schedule")
  .description("Run deduplication on a schedule")
  .argument("<files...>", "data files to process")
  .option("-c, --config <path>", "config YAML path")
  .option("--every <spec>", "run interval (e.g. 1h, 30m, 6h, 1d)")
  .option("--cron <spec>", "cron schedule (e.g. '0 6 * * *')")
  .option("--output-dir <dir>", "output directory", ".")
  .option("--max-runs <n>", "stop after N runs (default: run until interrupted)", (v) =>
    parseInt(v, 10),
  )
  .action(
    async (
      files: string[],
      opts: { config?: string; every?: string; cron?: string; outputDir: string; maxRuns?: number },
    ) => {
      const { ScheduledJob, parseInterval, parseCron } = await import("./node/scheduler.js");
      if (!opts.every && !opts.cron) {
        process.stderr.write("Error: specify --every or --cron\n");
        process.exit(1);
      }
      let interval: number;
      try {
        interval = opts.every ? parseInterval(opts.every) : parseCron(opts.cron!);
      } catch (err) {
        process.stderr.write(`Error: ${(err as Error).message}\n`);
        process.exit(1);
      }
      if (opts.cron) {
        // Be explicit rather than let an operator assume real cron semantics.
        process.stderr.write(
          "Note: --cron is simplified (interval only, not wall-clock scheduling), " +
            "matching the Python CLI. Use system cron for exact times.\n",
        );
      }

      const job = new ScheduledJob({
        jobId: `gm-${randomUUID().replace(/-/g, "").slice(0, 8)}`,
        filePaths: files,
        ...(opts.config ? { config: loadConfigFile(opts.config) } : {}),
        intervalSeconds: interval,
        outputDir: opts.outputDir,
        loadRows: (paths) => loadFilesWithSource(paths),
        out: (s) => process.stdout.write(s + "\n"),
      });
      // Ctrl+C finishes the current run's bookkeeping instead of hard-killing.
      process.on("SIGINT", () => {
        process.stdout.write("\nStopping after the current run...\n");
        job.stop();
      });
      await job.run(opts.maxRuns !== undefined ? { maxRuns: opts.maxRuns } : {});
    },
  );

// ---------- init (interactive config wizard) ----------
program
  .command("init")
  .description("Launch the interactive config wizard")
  .option("-o, --output <path>", "output path for the generated config")
  .action(async (opts: { output?: string }) => {
    const { runWizard, toYaml } = await import("./node/config-wizard.js");
    const { createStdinAsk, askYesNo, askWithDefault } = await import("./node/interactive.js");
    const { ask, close } = createStdinAsk();
    try {
      const config = await runWizard(ask, (s) => process.stdout.write(s + "\n"));
      let target = opts.output;
      if (!target) {
        // Python asks before saving when no --output was given; same here.
        target = (await askYesNo(ask, "\nSave config to file?", true))
          ? await askWithDefault(ask, "Output path", "goldenmatch.yaml")
          : undefined;
      }
      if (target) {
        mkdirSync(dirname(target), { recursive: true });
        writeFileSync(target, toYaml(config), "utf-8");
        process.stdout.write(`\nConfig saved to ${target}\n`);
      } else {
        process.stdout.write("\n" + toYaml(config));
      }
    } finally {
      close();
    }
  });

// ---------- label (build ground truth interactively) ----------
program
  .command("label")
  .description("Build ground truth by labeling record pairs interactively")
  .argument("<files...>", "input file paths")
  .requiredOption("-c, --config <path>", "config YAML path")
  .option("-o, --output <path>", "output ground-truth CSV", "ground_truth.csv")
  .option("-n, --n <count>", "number of pairs to label", (v) => parseInt(v, 10), 50)
  .option("--strategy <name>", "pair selection: borderline, random, or hardest", "borderline")
  .option("-a, --append", "append to an existing ground-truth file")
  .action(
    async (
      files: string[],
      opts: { config: string; output: string; n: number; strategy: string; append?: boolean },
    ) => {
      const { selectPairs, runLabelSession } = await import("./node/label-session.js");
      const { createStdinAsk } = await import("./node/interactive.js");
      const strategy = opts.strategy as "borderline" | "random" | "hardest";
      if (!["borderline", "random", "hardest"].includes(strategy)) {
        process.stderr.write("Error: --strategy must be borderline, random, or hardest\n");
        process.exit(2);
      }

      const rows = loadFilesWithSource(files);
      process.stdout.write("Running pipeline to generate candidate pairs...\n");
      const result = await dedupe(rows, { config: loadConfigFile(opts.config) });
      if (result.scoredPairs.length === 0) {
        process.stderr.write("No pairs found. Check your config.\n");
        process.exit(1);
      }

      const rowsById = new Map<number, Row>(rows.map((r, i) => [i, r]));
      const displayColumns = Object.keys(rows[0] ?? {})
        .filter((c) => !c.startsWith("__"))
        .slice(0, 6);

      // --append: skip pairs already present in the existing file (either orientation).
      const existing = new Set<string>();
      if (opts.append && existsSync(opts.output)) {
        for (const r of readFile(opts.output)) {
          existing.add(`${Number(r["id_a"])}:${Number(r["id_b"])}`);
        }
        process.stdout.write(`Loaded ${existing.size} existing labels from ${opts.output}\n`);
      }

      const { ask, close } = createStdinAsk();
      let session;
      try {
        process.stdout.write(`\nLabel ${opts.n} pairs. Type: y=match, n=no match, s=skip, q=quit\n\n`);
        session = await runLabelSession({
          pairs: selectPairs(result.scoredPairs, strategy),
          rowsById,
          displayColumns,
          target: opts.n,
          ask,
          existing,
          out: (s) => process.stdout.write(s + "\n"),
        });
      } finally {
        close();
      }

      if (session.labels.length === 0) {
        process.stdout.write("\nNo labels saved.\n");
        return;
      }
      const prior = opts.append && existsSync(opts.output) ? readFile(opts.output) : [];
      writeOutputRows(opts.output, [...prior, ...session.labels] as unknown as Row[], "csv");
      const matches = session.labels.filter((l) => l.label === 1).length;
      process.stdout.write(`\nSaved ${session.labels.length} labels to ${opts.output}\n`);
      process.stdout.write(
        `  Matches: ${matches}, Non-matches: ${session.labels.length - matches}, Skipped: ${session.skipped}\n`,
      );
    },
  );

// ---------- review (steward the borderline band) ----------
program
  .command("review")
  .description("Review borderline pairs and record approve/reject decisions")
  .argument("<files...>", "input file paths")
  .requiredOption("-c, --config <path>", "config YAML path")
  .option("--memory-path <path>", "Learning Memory SQLite path", ".goldenmatch/memory.db")
  .option("--merge-threshold <value>", "scores above this auto-merge (skip review)", parseFloat, 0.95)
  .option("--reject-below <value>", "scores below this are rejected outright", parseFloat, 0.75)
  .option("--decided-by <who>", "steward identifier recorded on each decision", "cli")
  .option("-n, --limit <count>", "max pairs to review this session", (v) => parseInt(v, 10), 50)
  .action(
    async (
      files: string[],
      opts: {
        config: string;
        memoryPath: string;
        mergeThreshold: number;
        rejectBelow: number;
        decidedBy: string;
        limit: number;
      },
    ) => {
      const { gatePairs } = await import("./core/review-queue.js");
      const { runReviewSession } = await import("./node/label-session.js");
      const { createStdinAsk } = await import("./node/interactive.js");
      const { addCorrection } = await import("./node/memory/api.js");

      const rows = loadFilesWithSource(files);
      process.stdout.write("Running pipeline to generate candidate pairs...\n");
      const result = await dedupe(rows, { config: loadConfigFile(opts.config) });

      const gated = gatePairs(result.scoredPairs, {
        approveAbove: opts.mergeThreshold,
        rejectBelow: opts.rejectBelow,
      });
      if (gated.needsReview.length === 0) {
        process.stdout.write(
          `\nNothing to review: ${gated.autoApproved.length} auto-approved, ${gated.rejected.length} rejected.\n`,
        );
        return;
      }

      const rowsById = new Map<number, Row>(rows.map((r, i) => [i, r]));
      const displayColumns = Object.keys(rows[0] ?? {})
        .filter((c) => !c.startsWith("__"))
        .slice(0, 6);

      const { ask, close } = createStdinAsk();
      let session;
      try {
        process.stdout.write(
          `\n${gated.needsReview.length} pair(s) in the review band. ` +
            `Type: y=match, n=no match, s=skip, q=quit\n\n`,
        );
        session = await runReviewSession({
          items: gated.needsReview.map((i) => ({ idA: i.idA, idB: i.idB, score: i.score })),
          rowsById,
          displayColumns,
          ask,
          limit: opts.limit,
          out: (s) => process.stdout.write(s + "\n"),
        });
      } finally {
        close();
      }

      // Persist AFTER the loop so a mid-session quit still records what was decided.
      let written = 0;
      for (const d of session.decisions) {
        try {
          await addCorrection({
            idA: d.idA,
            idB: d.idB,
            decision: d.decision,
            source: "steward",
            path: opts.memoryPath,
          });
          written++;
        } catch (err) {
          process.stderr.write(`Failed to record ${d.idA}/${d.idB}: ${(err as Error).message}\n`);
        }
      }
      const approved = session.decisions.filter((d) => d.decision === "approve").length;
      process.stdout.write(
        `\nDone. Approved ${approved}, rejected ${session.decisions.length - approved}, skipped ${session.skipped}.\n`,
      );
      process.stdout.write(`${written} decision(s) recorded to Learning Memory (${opts.memoryPath}).\n`);
    },
  );

// ---------- anomalies ----------
program
  .command("anomalies")
  .description("Detect suspicious/fake records (test emails, bad ZIPs, placeholders)")
  .argument("<files...>", "input file paths (.csv, .tsv, .json, .jsonl)")
  .option("-s, --sensitivity <level>", "low, medium, or high", "medium")
  .option("-o, --output <path>", "write anomalies to a CSV instead of printing")
  .option("-n, --limit <n>", "max rows to print", (v) => parseInt(v, 10), 50)
  .action(
    async (
      files: string[],
      opts: { sensitivity: string; output?: string; limit: number },
    ) => {
      const { detectAnomalies, formatAnomalyReport } = await import("./core/anomaly.js");
      const rows = loadFilesWithSource(files);
      let anomalies;
      try {
        anomalies = detectAnomalies(rows, opts.sensitivity);
      } catch (err) {
        // Python raises ValueError on a bad sensitivity rather than silently
        // falling through to the most-sensitive behavior. Same contract here.
        process.stderr.write(`Error: ${(err as Error).message}\n`);
        process.exit(2);
      }

      if (opts.output) {
        writeOutputRows(opts.output, anomalies as unknown as Row[], "csv");
        process.stdout.write(
          `Wrote ${anomalies.length} anomal${anomalies.length === 1 ? "y" : "ies"} to ${opts.output}\n`,
        );
        return;
      }

      process.stdout.write(formatAnomalyReport(anomalies.slice(0, opts.limit)) + "\n");
      if (anomalies.length > opts.limit) {
        process.stdout.write(
          `(showing ${opts.limit} of ${anomalies.length}; use --limit to see more)\n`,
        );
      }
    },
  );

// ---------- sensitivity ----------
program
  .command("sensitivity")
  .description("Analyze parameter sensitivity using CCMS cluster comparison")
  .argument("<files...>", "input file paths (.csv, .tsv, .json, .jsonl)")
  .requiredOption("-c, --config <path>", "config YAML path")
  .requiredOption(
    "-s, --sweep <spec>",
    "sweep spec 'field:start:stop:step' (repeatable)",
    (v: string, prev: string[]) => [...prev, v],
    [] as string[],
  )
  .option("--sample <n>", "random sample size for speed", (v) => parseInt(v, 10))
  .option("-o, --output <path>", "save results to JSON")
  .action(
    async (
      files: string[],
      opts: { config: string; sweep: string[]; sample?: number; output?: string },
    ) => {
      const { runSensitivitySweep, sweepStabilityReport } = await import(
        "./core/sensitivity.js"
      );
      // Python's spec grammar: field:start:stop:step (all numeric after the field).
      const specs = opts.sweep.map((raw) => {
        const parts = raw.split(":");
        if (parts.length !== 4) {
          process.stderr.write(
            `Error: bad --sweep '${raw}'; expected field:start:stop:step\n`,
          );
          process.exit(2);
        }
        const [field, start, stop, step] = parts as [string, string, string, string];
        const nums = [start, stop, step].map(parseFloat);
        if (nums.some((n) => !Number.isFinite(n))) {
          process.stderr.write(`Error: non-numeric range in --sweep '${raw}'\n`);
          process.exit(2);
        }
        return { field, start: nums[0]!, stop: nums[1]!, step: nums[2]! };
      });

      const rows = loadFilesWithSource(files);
      const config = loadConfigFile(opts.config);
      const results = await runSensitivitySweep(
        rows,
        config,
        specs,
        opts.sample,
      );

      const report = { results: results.map((r) => sweepStabilityReport(r)) };
      for (let i = 0; i < results.length; i++) {
        const r = results[i]!;
        const s = report.results[i]!;
        process.stdout.write(
          `${r.param.field}: baseline=${r.baselineValue} ` +
            `best=${s.best_value} (${s.best_unchanged_pct.toFixed(1)}% unchanged)\n`,
        );
        for (const p of s.points) {
          process.stdout.write(
            `    ${p.value}: unchanged=${p.unchanged} merged=${p.merged} ` +
              `partitioned=${p.partitioned} overlapping=${p.overlapping} twi=${p.twi.toFixed(4)}\n`,
          );
        }
      }
      if (opts.output) {
        writeFileSync(opts.output, JSON.stringify(report, null, 2) + "\n", "utf-8");
        process.stdout.write(`Results saved to ${opts.output}\n`);
      }
    },
  );

// ---------- pprl (privacy-preserving record linkage) ----------
//
// Python's `pprl` is a Typer sub-app with `link` + `auto-config`. Only `link`
// is ported: the TS package has the linkage protocol (`runPPRL`) but NOT the
// PPRL auto-config profiler, which remains tracked as the `pprl_auto_config`
// python_only MCP tool. `pprl auto-config` therefore errors with a pointer
// rather than silently doing something different.
const pprlCmd = program
  .command("pprl")
  .description("Privacy-preserving record linkage (bloom-filter CLKs)");

pprlCmd
  .command("link")
  .description("Link two parties' records without sharing raw values")
  .requiredOption("-a, --file-a <path>", "party A data file")
  .requiredOption("-b, --file-b <path>", "party B data file")
  .requiredOption("-f, --fields <fields>", "comma-separated fields to match on")
  .option("-t, --threshold <value>", "match threshold", parseFloat, 0.85)
  .option("-s, --security <level>", "standard | high | paranoid", "high")
  .option("-p, --protocol <name>", "trusted_third_party | smc", "trusted_third_party")
  .option("--scorer <name>", "dice | jaccard", "dice")
  .option("--salt <key>", "shared HMAC key (both parties must use the same)")
  .option("-o, --output <path>", "output CSV of cluster assignments")
  .action(
    async (opts: {
      fileA: string;
      fileB: string;
      fields: string;
      threshold: number;
      security: string;
      protocol: string;
      scorer: string;
      salt?: string;
      output?: string;
    }) => {
      const { runPPRL } = await import("./core/pprl/protocol.js");
      const security = opts.security as "standard" | "high" | "paranoid";
      const protocol = opts.protocol as "trusted_third_party" | "smc";
      const scorer = opts.scorer as "dice" | "jaccard";
      if (!["standard", "high", "paranoid"].includes(security)) {
        process.stderr.write(`Error: --security must be standard|high|paranoid\n`);
        process.exit(2);
      }
      if (!["trusted_third_party", "smc"].includes(protocol)) {
        process.stderr.write(`Error: --protocol must be trusted_third_party|smc\n`);
        process.exit(2);
      }

      const rowsA = readFile(opts.fileA);
      const rowsB = readFile(opts.fileB);
      const result = runPPRL(rowsA, rowsB, {
        fields: parseCsvList(opts.fields),
        securityLevel: security,
        protocol,
        threshold: opts.threshold,
        scorer,
        ...(opts.salt ? { salt: opts.salt } : {}),
      });

      process.stdout.write(
        `PPRL (${protocol}, ${security}): ${result.matchCount} match(es) ` +
          `over ${result.totalComparisons} comparison(s), ${result.clusters.length} cluster(s)\n`,
      );
      if (opts.output) {
        // One row per cluster member: cluster_id + which party + that party's row id.
        const rows: Row[] = [];
        result.clusters.forEach((members, cid) => {
          for (const m of members) {
            rows.push({ cluster_id: cid, party: m.party, record_id: m.id });
          }
        });
        writeOutputRows(opts.output, rows, "csv");
        process.stdout.write(`Cluster assignments written to ${opts.output}\n`);
      }
    },
  );

pprlCmd
  .command("auto-config")
  .description("(not ported) profile a file and suggest PPRL parameters")
  .argument("[file]", "data file to analyze")
  .action(() => {
    process.stderr.write(
      "pprl auto-config is not available in the TypeScript package.\n" +
        "The PPRL auto-config profiler is Python-only (`pprl_auto_config`).\n" +
        "Use: goldenmatch pprl auto-config <file>   (Python)\n",
    );
    process.exit(2);
  });

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  program.parseAsync(process.argv).catch((err: unknown) => {
    const message = err instanceof Error ? err.message : String(err);
    process.stderr.write(`Error: ${message}\n`);
    process.exit(1);
  });
}
