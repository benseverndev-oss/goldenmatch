/**
 * Differ — compare two row arrays and report differences.
 */

import type { DiffResult, Row } from "../types.js";

export function diffDataframes(
  before: readonly Row[],
  after: readonly Row[],
): DiffResult {
  const beforeCols = new Set(before.length > 0 ? Object.keys(before[0]!) : []);
  const afterCols = new Set(after.length > 0 ? Object.keys(after[0]!) : []);

  const addedColumns = [...afterCols].filter((c) => !beforeCols.has(c)).sort();
  const removedColumns = [...beforeCols].filter((c) => !afterCols.has(c)).sort();
  const commonCols = [...beforeCols].filter((c) => afterCols.has(c)).sort();

  const changedColumns: string[] = [];
  const columnDetails: Record<string, { changedRows: number }> = {};
  let totalChanges = 0;

  for (const col of commonCols) {
    if (before.length !== after.length) {
      changedColumns.push(col);
      totalChanges += Math.abs(before.length - after.length);
      continue;
    }

    let changes = 0;
    for (let i = 0; i < before.length; i++) {
      const bVal = String(before[i]![col] ?? "");
      const aVal = String(after[i]![col] ?? "");
      if (bVal !== aVal) changes++;
    }

    if (changes > 0) {
      changedColumns.push(col);
      totalChanges += changes;
      columnDetails[col] = { changedRows: changes };
    }
  }

  return {
    totalChanges,
    changedColumns,
    addedColumns,
    removedColumns,
    rowCountBefore: before.length,
    rowCountAfter: after.length,
    columnDetails,
  };
}
