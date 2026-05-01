/**
 * File connector — read/write CSV files as Row[].
 * Node-only (uses node:fs).
 */

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { extname, dirname } from "node:path";
import type { Row } from "../../core/types.js";

/** Coerce a raw string to string | number | boolean.
 *  Preserves leading-zero strings (zip codes, IDs) as strings. */
function coerceValue(raw: string): string | number | boolean {
  if (raw === "true" || raw === "True" || raw === "TRUE") return true;
  if (raw === "false" || raw === "False" || raw === "FALSE") return false;
  if (raw.length > 0 && raw === raw.trim()) {
    // Don't coerce strings with leading zeros (zip codes, SSNs, phone numbers)
    // except "0" itself and decimal numbers like "0.5"
    if (raw.length > 1 && raw[0] === "0" && raw[1] !== ".") return raw;
    const n = Number(raw);
    if (Number.isFinite(n) && raw !== "") return n;
  }
  return raw;
}

function parseCsv(content: string): Row[] {
  const lines = content.split(/\r?\n/).filter((line) => line.trim());
  if (lines.length === 0) return [];

  const headers = parseCsvLine(lines[0]!);
  const rows: Row[] = [];

  for (let i = 1; i < lines.length; i++) {
    const values = parseCsvLine(lines[i]!);
    const row: Record<string, unknown> = {};
    for (let j = 0; j < headers.length; j++) {
      const raw = values[j] ?? "";
      row[headers[j]!] = raw === "" ? null : coerceValue(raw);
    }
    rows.push(row);
  }

  return rows;
}

function parseCsvLine(line: string): string[] {
  const result: string[] = [];
  let current = "";
  let inQuotes = false;

  for (let i = 0; i < line.length; i++) {
    const ch = line[i]!;
    if (inQuotes) {
      if (ch === '"') {
        if (i + 1 < line.length && line[i + 1] === '"') {
          current += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        current += ch;
      }
    } else {
      if (ch === '"') {
        inQuotes = true;
      } else if (ch === ",") {
        result.push(current);
        current = "";
      } else {
        current += ch;
      }
    }
  }
  result.push(current);
  return result;
}

function rowsToCsv(rows: readonly Row[]): string {
  if (rows.length === 0) return "";
  const headers = Object.keys(rows[0]!);
  const lines = [headers.join(",")];

  for (const row of rows) {
    const values = headers.map((h) => {
      const v = row[h];
      if (v === null || v === undefined) return "";
      const s = String(v);
      if (s.includes(",") || s.includes('"') || s.includes("\n")) {
        return `"${s.replace(/"/g, '""')}"`;
      }
      return s;
    });
    lines.push(values.join(","));
  }

  return lines.join("\n") + "\n";
}

export function readFile(path: string): Row[] {
  const ext = extname(path).toLowerCase();
  if (ext === ".json") {
    const content = readFileSync(path, "utf-8");
    return JSON.parse(content) as Row[];
  }
  if (ext !== ".csv") {
    throw new Error(`Unsupported file format: ${ext}. Supported: .csv, .json`);
  }
  const content = readFileSync(path, "utf-8");
  return parseCsv(content);
}

export function writeFile(rows: readonly Row[], path: string): void {
  const ext = extname(path).toLowerCase();
  const dir = dirname(path);
  mkdirSync(dir, { recursive: true });

  if (ext === ".json") {
    writeFileSync(path, JSON.stringify(rows, null, 2));
    return;
  }
  if (ext !== ".csv") {
    throw new Error(`Unsupported file format: ${ext}. Supported: .csv, .json`);
  }
  writeFileSync(path, rowsToCsv(rows));
}
