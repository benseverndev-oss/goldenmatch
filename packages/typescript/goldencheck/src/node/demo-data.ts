/**
 * Node-only helper: write the demo dataset to a CSV file.
 * Port of goldencheck/cli/demo_data.py's `generate_demo_csv`.
 */

import { writeFileSync, mkdtempSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import type { Row } from "../core/data.js";
import { generateDemoRecords, type DemoOptions } from "../core/cli/demo-data.js";

const DEMO_COLUMNS = [
  "customer_id",
  "name",
  "email",
  "age",
  "phone",
  "status",
  "purchase_amount",
] as const;

/** RFC-4180-ish CSV cell escaping. */
function csvCell(v: unknown): string {
  if (v === null || v === undefined) return "";
  const s = String(v);
  if (s.includes(",") || s.includes('"') || s.includes("\n") || s.includes("\r")) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

/** Serialise demo records to a CSV string. */
function recordsToCsv(records: readonly Row[]): string {
  const lines: string[] = [DEMO_COLUMNS.join(",")];
  for (const r of records) {
    lines.push(DEMO_COLUMNS.map((c) => csvCell(r[c])).join(","));
  }
  return lines.join("\n") + "\n";
}

/**
 * Generate a CSV with realistic data-quality issues and write it to disk.
 * Returns the path written. When `path` is omitted, writes to a fresh temp dir.
 */
export function generateDemoCsv(path?: string, options?: DemoOptions): string {
  const records = generateDemoRecords(options);
  const target = path ?? join(mkdtempSync(join(tmpdir(), "goldencheck-demo-")), "demo_data.csv");
  writeFileSync(target, recordsToCsv(records), "utf-8");
  return target;
}
