// Minimal RFC 4180 CSV parser. Edge-safe (no Node built-ins).
//
// Supports:
//   - Header row (always required)
//   - Quoted fields with embedded commas, newlines, and "" escapes
//   - CRLF, LF, and CR line endings
//   - Trailing newline tolerance
//
// Does NOT support:
//   - Custom delimiters (comma only)
//   - Comment lines
//   - Skipping rows
//
// If we need more, we'll revisit. The Python side uses polars CSV reader;
// parity tests cover fixture-based compatibility.

export interface CsvParseResult {
  headers: string[];
  rows: Record<string, string>[];
}

export function parseCsv(text: string): CsvParseResult {
  // Strip UTF-8 BOM if present
  if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);

  const records: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;
  let i = 0;
  const n = text.length;

  while (i < n) {
    const ch = text[i]!;

    if (inQuotes) {
      if (ch === '"') {
        if (i + 1 < n && text[i + 1] === '"') {
          field += '"';
          i += 2;
          continue;
        }
        inQuotes = false;
        i++;
        continue;
      }
      field += ch;
      i++;
      continue;
    }

    if (ch === '"') {
      inQuotes = true;
      i++;
      continue;
    }
    if (ch === ",") {
      row.push(field);
      field = "";
      i++;
      continue;
    }
    if (ch === "\r") {
      // Handle CRLF as single line ending
      row.push(field);
      field = "";
      records.push(row);
      row = [];
      i++;
      if (i < n && text[i] === "\n") i++;
      continue;
    }
    if (ch === "\n") {
      row.push(field);
      field = "";
      records.push(row);
      row = [];
      i++;
      continue;
    }
    field += ch;
    i++;
  }

  // Flush final field/row if not empty
  if (field.length > 0 || row.length > 0) {
    row.push(field);
    records.push(row);
  }

  // Drop trailing empty row (from trailing newline)
  if (
    records.length > 0 &&
    records[records.length - 1]!.length === 1 &&
    records[records.length - 1]![0] === ""
  ) {
    records.pop();
  }

  if (records.length === 0) return { headers: [], rows: [] };

  const headers = records[0]!;
  const rows: Record<string, string>[] = [];
  for (let r = 1; r < records.length; r++) {
    const record = records[r]!;
    const obj: Record<string, string> = {};
    for (let c = 0; c < headers.length; c++) {
      obj[headers[c]!] = record[c] ?? "";
    }
    rows.push(obj);
  }
  return { headers, rows };
}
