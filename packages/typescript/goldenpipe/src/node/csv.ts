/**
 * Minimal CSV reader for the node `run(source)` path.
 * Handles quoted fields, embedded commas, embedded newlines, and "" escapes.
 *
 * Node-only: reads from the filesystem.
 */

import { readFileSync } from "node:fs";
import type { Row } from "../core/index.js";

/** Parse a CSV string into Row[]. First line is the header. */
export function parseCsv(content: string): Row[] {
  const records = parseRecords(content);
  if (records.length === 0) return [];
  const headers = records[0]!;
  const rows: Row[] = [];
  for (let i = 1; i < records.length; i++) {
    const values = records[i]!;
    // Skip fully-empty trailing lines.
    if (values.length === 1 && values[0] === "") continue;
    const row: Row = {};
    for (let c = 0; c < headers.length; c++) {
      row[headers[c]!] = c < values.length ? values[c]! : "";
    }
    rows.push(row);
  }
  return rows;
}

/** Read and parse a CSV file. */
export function readCsv(path: string): Row[] {
  const content = readFileSync(path, "utf8");
  return parseCsv(content);
}

/** Tokenize a CSV document into rows of string cells. */
function parseRecords(content: string): string[][] {
  const records: string[][] = [];
  let field = "";
  let record: string[] = [];
  let inQuotes = false;
  let i = 0;
  const n = content.length;

  const pushField = (): void => {
    record.push(field);
    field = "";
  };
  const pushRecord = (): void => {
    pushField();
    records.push(record);
    record = [];
  };

  while (i < n) {
    const ch = content[i]!;
    if (inQuotes) {
      if (ch === '"') {
        if (i + 1 < n && content[i + 1] === '"') {
          field += '"';
          i += 2;
          continue;
        }
        inQuotes = false;
        i += 1;
        continue;
      }
      field += ch;
      i += 1;
      continue;
    }
    if (ch === '"') {
      inQuotes = true;
      i += 1;
      continue;
    }
    if (ch === ",") {
      pushField();
      i += 1;
      continue;
    }
    if (ch === "\r") {
      // Handle CRLF and lone CR.
      if (i + 1 < n && content[i + 1] === "\n") i += 1;
      pushRecord();
      i += 1;
      continue;
    }
    if (ch === "\n") {
      pushRecord();
      i += 1;
      continue;
    }
    field += ch;
    i += 1;
  }

  // Flush the final record if the file didn't end with a newline.
  if (field !== "" || record.length > 0) {
    pushRecord();
  }
  return records;
}
