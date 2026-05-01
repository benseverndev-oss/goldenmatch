/**
 * Build representative sample blocks from TabularData + findings.
 * Port of goldencheck/llm/sample_block.py.
 * Edge-safe: no Node.js dependencies.
 */

import type { TabularData } from "../data.js";
import type { Finding } from "../types.js";
import { createRng } from "../stats.js";

export interface ValueCount {
  value: string;
  count: number;
}

export interface ExistingFinding {
  severity: string;
  check: string;
  message: string;
}

export interface SampleBlock {
  column: string;
  dtype: string;
  row_count: number;
  null_count: number;
  null_pct: number;
  unique_count: number;
  top_values: ValueCount[];
  rare_values: ValueCount[];
  random_sample: string[];
  flagged_values: string[];
  existing_findings: ExistingFinding[];
}

/**
 * Build a representative sample block for each column.
 * Prioritizes columns with the most findings when exceeding maxColumns.
 */
export function buildSampleBlocks(
  data: TabularData,
  findings: readonly Finding[],
  maxColumns: number = 50,
  focusColumns?: ReadonlySet<string>,
): Record<string, SampleBlock> {
  // Deterministic RNG with seed=42
  const rng = createRng(42);

  // Filter columns
  let columns = [...data.columns];
  if (focusColumns) {
    columns = columns.filter((c) => focusColumns.has(c));
  }

  // If too many columns, prioritize those with most findings
  if (columns.length > maxColumns) {
    const findingCounts = new Map<string, number>();
    for (const f of findings) {
      findingCounts.set(f.column, (findingCounts.get(f.column) ?? 0) + 1);
    }
    columns.sort((a, b) => (findingCounts.get(b) ?? 0) - (findingCounts.get(a) ?? 0));
    columns = columns.slice(0, maxColumns);
  }

  // Index findings by column
  const findingsByCol = new Map<string, Finding[]>();
  for (const f of findings) {
    let arr = findingsByCol.get(f.column);
    if (!arr) {
      arr = [];
      findingsByCol.set(f.column, arr);
    }
    arr.push(f);
  }

  const blocks: Record<string, SampleBlock> = {};

  for (const colName of columns) {
    const nonNull = data.dropNulls(colName);
    const nullCount = data.nullCount(colName);
    const rowCount = data.rowCount;

    const block: SampleBlock = {
      column: colName,
      dtype: data.dtype(colName),
      row_count: rowCount,
      null_count: nullCount,
      null_pct: rowCount > 0 ? Math.round((nullCount / rowCount) * 1000) / 1000 : 0,
      unique_count: data.nUnique(colName),
      top_values: [],
      rare_values: [],
      random_sample: [],
      flagged_values: [],
      existing_findings: [],
    };

    if (nonNull.length > 0) {
      // Value counts sorted descending
      const vc = data.valueCounts(colName);
      const sorted = [...vc.entries()].sort((a, b) => b[1] - a[1]);

      // Top values (most frequent)
      block.top_values = sorted.slice(0, 5).map(([value, count]) => ({
        value: String(value),
        count,
      }));

      // Rare values (least frequent)
      block.rare_values = sorted.slice(-5).map(([value, count]) => ({
        value: String(value),
        count,
      }));

      // Random sample from non-null values (Fisher-Yates partial shuffle)
      const allVals = nonNull.map(String);
      const sampleSize = Math.min(5, allVals.length);
      const indices = Array.from({ length: allVals.length }, (_, i) => i);
      for (let i = 0; i < sampleSize; i++) {
        const j = i + Math.floor(rng() * (indices.length - i));
        [indices[i], indices[j]] = [indices[j]!, indices[i]!];
      }
      block.random_sample = indices.slice(0, sampleSize).map((idx) => allVals[idx]!);
    }

    // Flagged values from profiler findings
    const colFindings = findingsByCol.get(colName) ?? [];
    const flagged = new Set<string>();
    for (const f of colFindings) {
      for (const sv of f.sampleValues) {
        flagged.add(sv);
      }
    }
    block.flagged_values = [...flagged];

    // Existing findings summary
    block.existing_findings = colFindings.map((f) => ({
      severity: severityToLabel(f.severity),
      check: f.check,
      message: f.message,
    }));

    blocks[colName] = block;
  }

  return blocks;
}

// --- Helper ---

function severityToLabel(sev: number): string {
  switch (sev) {
    case 3:
      return "error";
    case 2:
      return "warning";
    case 1:
      return "info";
    default:
      return "warning";
  }
}
