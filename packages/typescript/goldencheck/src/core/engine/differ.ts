/**
 * Differ — compares two dataset versions.
 * Port of goldencheck/engine/differ.py.
 */

import type { TabularData } from "../data.js";
import type { Finding, DatasetProfile } from "../types.js";
import { Severity } from "../types.js";

export interface SchemaChange {
  readonly type: "added" | "removed" | "type_changed";
  readonly column: string;
  readonly oldType?: string | undefined;
  readonly newType?: string | undefined;
}

export interface FindingChange {
  readonly type: "new" | "resolved" | "worsened" | "improved";
  readonly column: string;
  readonly check: string;
  readonly oldSeverity?: string | undefined;
  readonly newSeverity?: string | undefined;
  readonly message: string;
}

export interface StatChange {
  readonly metric: string;
  readonly oldValue: number;
  readonly newValue: number;
  readonly delta: number;
}

export interface DiffReport {
  readonly schemaChanges: readonly SchemaChange[];
  readonly findingChanges: readonly FindingChange[];
  readonly statChanges: readonly StatChange[];
}

/**
 * Compare two datasets and their findings.
 */
export function diffData(
  oldData: TabularData,
  newData: TabularData,
  oldFindings: readonly Finding[],
  newFindings: readonly Finding[],
): DiffReport {
  const schemaChanges: SchemaChange[] = [];
  const findingChanges: FindingChange[] = [];
  const statChanges: StatChange[] = [];

  // Schema changes
  const oldCols = new Set(oldData.columns);
  const newCols = new Set(newData.columns);

  for (const col of newCols) {
    if (!oldCols.has(col)) {
      schemaChanges.push({ type: "added", column: col, newType: newData.dtype(col) });
    }
  }
  for (const col of oldCols) {
    if (!newCols.has(col)) {
      schemaChanges.push({ type: "removed", column: col, oldType: oldData.dtype(col) });
    }
  }
  for (const col of oldCols) {
    if (newCols.has(col)) {
      const oldType = oldData.dtype(col);
      const newType = newData.dtype(col);
      if (oldType !== newType) {
        schemaChanges.push({ type: "type_changed", column: col, oldType, newType });
      }
    }
  }

  // Finding changes — match by (column, check), supporting multiple per key
  const oldGroups = new Map<string, Finding[]>();
  for (const f of oldFindings) {
    const key = `${f.column}|${f.check}`;
    const arr = oldGroups.get(key);
    if (arr) arr.push(f);
    else oldGroups.set(key, [f]);
  }
  const newGroups = new Map<string, Finding[]>();
  for (const f of newFindings) {
    const key = `${f.column}|${f.check}`;
    const arr = newGroups.get(key);
    if (arr) arr.push(f);
    else newGroups.set(key, [f]);
  }

  for (const [key, newFs] of newGroups) {
    const oldFs = oldGroups.get(key);
    if (!oldFs) {
      // All new
      for (const newF of newFs) {
        findingChanges.push({
          type: "new",
          column: newF.column,
          check: newF.check,
          newSeverity: severityName(newF.severity),
          message: newF.message,
        });
      }
    } else {
      // Compare by index (best-effort pairing)
      const maxLen = Math.max(newFs.length, oldFs.length);
      for (let i = 0; i < maxLen; i++) {
        const newF = newFs[i];
        const oldF = oldFs[i];
        if (newF && !oldF) {
          findingChanges.push({ type: "new", column: newF.column, check: newF.check, newSeverity: severityName(newF.severity), message: newF.message });
        } else if (!newF && oldF) {
          findingChanges.push({ type: "resolved", column: oldF.column, check: oldF.check, oldSeverity: severityName(oldF.severity), message: oldF.message });
        } else if (newF && oldF) {
          if (newF.severity > oldF.severity) {
            findingChanges.push({ type: "worsened", column: newF.column, check: newF.check, oldSeverity: severityName(oldF.severity), newSeverity: severityName(newF.severity), message: newF.message });
          } else if (newF.severity < oldF.severity) {
            findingChanges.push({ type: "improved", column: newF.column, check: newF.check, oldSeverity: severityName(oldF.severity), newSeverity: severityName(newF.severity), message: newF.message });
          }
        }
      }
    }
  }

  for (const [key, oldFs] of oldGroups) {
    if (!newGroups.has(key)) {
      for (const oldF of oldFs) {
        findingChanges.push({
          type: "resolved",
          column: oldF.column,
          check: oldF.check,
          oldSeverity: severityName(oldF.severity),
          message: oldF.message,
        });
      }
    }
  }

  // Stat changes
  const rowDelta = newData.rowCount - oldData.rowCount;
  if (rowDelta !== 0) {
    statChanges.push({ metric: "row_count", oldValue: oldData.rowCount, newValue: newData.rowCount, delta: rowDelta });
  }
  const colDelta = newData.columns.length - oldData.columns.length;
  if (colDelta !== 0) {
    statChanges.push({ metric: "column_count", oldValue: oldData.columns.length, newValue: newData.columns.length, delta: colDelta });
  }

  return { schemaChanges, findingChanges, statChanges };
}

function severityName(s: Severity): string {
  switch (s) {
    case Severity.ERROR: return "ERROR";
    case Severity.WARNING: return "WARNING";
    case Severity.INFO: return "INFO";
  }
}

/**
 * Format a DiffReport as a human-readable string.
 */
export function formatDiffReport(report: DiffReport): string {
  const lines: string[] = [];

  if (report.schemaChanges.length > 0) {
    lines.push("Schema Changes:");
    for (const c of report.schemaChanges) {
      if (c.type === "added") lines.push(`  + ${c.column} (${c.newType})`);
      else if (c.type === "removed") lines.push(`  - ${c.column} (${c.oldType})`);
      else lines.push(`  ~ ${c.column}: ${c.oldType} → ${c.newType}`);
    }
  }

  if (report.findingChanges.length > 0) {
    lines.push("Finding Changes:");
    for (const c of report.findingChanges) {
      const prefix = c.type === "new" ? "+" : c.type === "resolved" ? "-" : "~";
      lines.push(`  ${prefix} [${c.type}] ${c.column}/${c.check}: ${c.message}`);
    }
  }

  if (report.statChanges.length > 0) {
    lines.push("Stat Changes:");
    for (const c of report.statChanges) {
      const sign = c.delta > 0 ? "+" : "";
      lines.push(`  ${c.metric}: ${c.oldValue} → ${c.newValue} (${sign}${c.delta})`);
    }
  }

  return lines.join("\n");
}
