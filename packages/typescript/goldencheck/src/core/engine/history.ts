/**
 * Scan history — append-only JSONL log of scan results.
 * Port of goldencheck/engine/history.py.
 * Types are edge-safe; read/write functions require Node.js (node:fs).
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { resolve, join, dirname } from "node:path";

import type { Finding, DatasetProfile } from "../types.js";
import { Severity, healthScore } from "../types.js";

// --- Types ---

export interface ScanRecord {
  readonly timestamp: string; // ISO 8601
  readonly file: string;
  readonly rows: number;
  readonly columns: number;
  readonly grade: string;
  readonly score: number;
  readonly errors: number;
  readonly warnings: number;
  readonly findingsCount: number;
}

// --- Constants ---

const HISTORY_DIR = ".goldencheck";
const HISTORY_FILE = join(HISTORY_DIR, "history.jsonl");

// --- Public API ---

/**
 * Append a scan record to `.goldencheck/history.jsonl`.
 * Computes health grade from findings using per-column capped scoring.
 */
export function recordScan(
  file: string,
  profile: DatasetProfile,
  findings: readonly Finding[],
): void {
  const errors = findings.filter((f) => f.severity === Severity.ERROR).length;
  const warnings = findings.filter((f) => f.severity === Severity.WARNING).length;

  // Build per-column breakdown for capped health score
  const byCol: Record<string, { errors: number; warnings: number }> = {};
  for (const f of findings) {
    if (f.severity >= Severity.WARNING) {
      if (!byCol[f.column]) {
        byCol[f.column] = { errors: 0, warnings: 0 };
      }
      if (f.severity === Severity.ERROR) {
        byCol[f.column]!.errors += 1;
      } else {
        byCol[f.column]!.warnings += 1;
      }
    }
  }

  const hs = healthScore(byCol);

  const record: Record<string, unknown> = {
    timestamp: new Date().toISOString().replace(/\.\d{3}Z$/, ""),
    file: resolve(file),
    rows: profile.rowCount,
    columns: profile.columnCount,
    grade: hs.grade,
    score: hs.points,
    errors,
    warnings,
    findingsCount: findings.length,
  };

  try {
    mkdirSync(HISTORY_DIR, { recursive: true });
    writeFileSync(HISTORY_FILE, JSON.stringify(record) + "\n", { flag: "a" });
  } catch (e) {
    console.warn("Failed to write scan history:", e);
  }
}

/**
 * Load scan records from history.
 * Optionally filter by file path and limit to the last N records.
 */
export function loadHistory(fileFilter?: string, lastN?: number): ScanRecord[] {
  if (!existsSync(HISTORY_FILE)) {
    return [];
  }

  let content: string;
  try {
    content = readFileSync(HISTORY_FILE, "utf-8");
  } catch {
    return [];
  }

  const records: ScanRecord[] = [];
  for (const line of content.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    try {
      const data = JSON.parse(trimmed) as Record<string, unknown>;
      const record: ScanRecord = {
        timestamp: String(data.timestamp ?? ""),
        file: String(data.file ?? ""),
        rows: Number(data.rows ?? 0),
        columns: Number(data.columns ?? 0),
        grade: String(data.grade ?? ""),
        score: Number(data.score ?? 0),
        errors: Number(data.errors ?? 0),
        warnings: Number(data.warnings ?? 0),
        findingsCount: Number(data.findingsCount ?? data.findings_count ?? 0),
      };

      if (fileFilter && record.file !== fileFilter) {
        continue;
      }
      records.push(record);
    } catch {
      // Skip malformed lines
      continue;
    }
  }

  if (lastN && lastN > 0) {
    return records.slice(-lastN);
  }

  return records;
}

/**
 * Get the most recent scan record for a given file.
 * Returns null if no previous scan exists.
 */
export function getPreviousScan(file: string): ScanRecord | null {
  const resolved = resolve(file);
  const records = loadHistory(resolved);
  return records.length > 0 ? records[records.length - 1]! : null;
}
