#!/usr/bin/env node
/**
 * cli.ts -- GoldenMatch command-line interface.
 *
 * Built on commander. Exposes `dedupe`, `match`, `score`, `profile`,
 * `info`, and `demo` subcommands.
 */

import { Command } from "commander";
import { extname, basename } from "node:path";
import {
  readFile,
  writeCsv,
  writeJson,
} from "./node/connectors/file.js";
import { dedupe, match, scoreStrings } from "./core/api.js";
import { loadConfigFile } from "./node/config-file.js";
import type { Row } from "./core/types.js";

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

const program = new Command();

program
  .name("goldenmatch-js")
  .description("Entity resolution toolkit -- dedupe, match, build golden records")
  .version("0.1.0");

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
  .action(async (files: string[], opts: SharedMatchOpts) => {
    const rows = loadFilesWithSource(files);
    const options = buildOptionsFromFlags(opts);
    const result = await dedupe(rows, options);
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
    process.stdout.write("GoldenMatch JS v0.1.0\n");
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

// ---------- tui ----------
program
  .command("tui")
  .description("Launch interactive TUI (requires optional peer deps: ink + react)")
  .argument("[files...]", "input files to load on startup")
  .option("-c, --config <path>", "path to YAML config file")
  .action(async (files: string[], opts: { config?: string }) => {
    try {
      const { startTui } = await import("./node/tui/app.js");
      const tuiOpts: { files?: string[]; config?: ReturnType<typeof loadConfigFile> } = {};
      if (files && files.length > 0) tuiOpts.files = files;
      if (opts.config) tuiOpts.config = loadConfigFile(opts.config);
      await startTui(tuiOpts);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      process.stderr.write(`TUI error: ${message}\n`);
      process.exit(1);
    }
  });

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

program.parseAsync(process.argv).catch((err: unknown) => {
  const message = err instanceof Error ? err.message : String(err);
  process.stderr.write(`Error: ${message}\n`);
  process.exit(1);
});
