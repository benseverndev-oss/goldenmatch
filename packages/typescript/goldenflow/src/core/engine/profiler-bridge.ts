/**
 * Profiler bridge — infer column types from data using regex heuristics.
 * Edge-safe (no Node dependencies).
 */

import type { ColumnValue, ColumnProfile, DatasetProfile, Row } from "../types.js";
import { makeColumnProfile } from "../types.js";
import { TabularData } from "../data.js";

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const PHONE_RE = /^[+(]?\d[\d()\-.\s]{6,18}\d$/;
const DATE_RE =
  /^(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})$/;
const NAME_RE = /^[A-Z][a-z]+(\s+[A-Z][a-z]+)+$/;
const ZIP_RE = /^\d{5}(-\d{4})?$/;

const NAME_PATTERNS: Record<string, readonly string[]> = {
  zip: ["zip", "postal", "zipcode", "zip_code", "postal_code"],
  phone: ["phone", "tel", "mobile", "cell", "fax"],
  email: ["email", "e_mail", "mail"],
  date: ["date", "created", "updated", "timestamp", "dob", "birth"],
  state: ["state", "province", "region"],
  name: ["first_name", "last_name", "fname", "lname", "full_name", "fullname"],
};

function overrideTypeByColumnName(columnName: string, currentType: string): string {
  if (currentType !== "string" && currentType !== "numeric") return currentType;
  const colLower = columnName.toLowerCase().replace(/-/g, "_");
  for (const [semanticType, patterns] of Object.entries(NAME_PATTERNS)) {
    for (const pattern of patterns) {
      if (colLower.includes(pattern)) return semanticType;
    }
  }
  return currentType;
}

function inferType(values: readonly ColumnValue[], columnName: string): string {
  // Check JS runtime types first
  const nonNull = values.filter((v): v is Exclude<ColumnValue, null> => v !== null);
  if (nonNull.length === 0) return "string";

  let hasNumber = false;
  let hasBoolean = false;

  for (const v of nonNull) {
    if (typeof v === "number") hasNumber = true;
    else if (typeof v === "boolean") hasBoolean = true;
  }

  if (hasNumber && !hasBoolean) return overrideTypeByColumnName(columnName, "numeric");
  if (hasBoolean && !hasNumber) return "boolean";

  // Sample string values for pattern matching
  const stringVals: string[] = [];
  for (const v of nonNull) {
    if (typeof v === "string") {
      const trimmed = v.trim();
      if (trimmed) stringVals.push(trimmed);
    }
  }
  if (stringVals.length === 0) return "string";

  const sample = stringVals.slice(0, 100);

  const checks: [string, RegExp, number][] = [
    ["email", EMAIL_RE, 0.7],
    ["zip", ZIP_RE, 0.7],
    ["date", DATE_RE, 0.5],
    ["phone", PHONE_RE, 0.6],
    ["name", NAME_RE, 0.5],
  ];

  for (const [typeName, pattern, threshold] of checks) {
    let matches = 0;
    for (const v of sample) {
      if (pattern.test(v)) matches++;
    }
    if (matches / sample.length >= threshold) {
      return overrideTypeByColumnName(columnName, typeName);
    }
  }

  return overrideTypeByColumnName(columnName, "string");
}

function profileColumn(data: TabularData, columnName: string): ColumnProfile {
  // Use rawColumn to avoid converting "N/A" etc. to null during profiling
  const values = data.rawColumn(columnName);
  const rowCount = values.length;
  let nullCount = 0;
  const nonNullValues: ColumnValue[] = [];
  const uniqueSet = new Set<ColumnValue>();
  for (const v of values) {
    if (v === null) { nullCount++; continue; }
    nonNullValues.push(v);
    uniqueSet.add(v);
  }
  const uniqueCount = uniqueSet.size;
  const sampleValues = nonNullValues
    .slice(0, 5)
    .map((v) => String(v));

  const inferredType = inferType(values, columnName);

  return makeColumnProfile({
    name: columnName,
    inferredType,
    rowCount,
    nullCount,
    nullPct: rowCount > 0 ? nullCount / rowCount : 0,
    uniqueCount,
    uniquePct: rowCount > 0 ? uniqueCount / rowCount : 0,
    sampleValues,
  });
}

export function profileDataframe(
  rows: readonly Row[],
  filePath = "",
): DatasetProfile {
  const data = new TabularData(rows);
  const columns = data.columns.map((col) => profileColumn(data, col));

  return {
    filePath,
    rowCount: data.rowCount,
    columnCount: data.columns.length,
    columns,
  };
}
