/**
 * Auto-fixer — applies safe/moderate/aggressive fixes to data.
 * Port of goldencheck/engine/fixer.py.
 */

import { TabularData, isNullish, type Row } from "../data.js";
import type { Finding } from "../types.js";

export interface FixEntry {
  readonly column: string;
  readonly fixType: string;
  readonly rowsAffected: number;
  readonly sampleBefore: readonly string[];
  readonly sampleAfter: readonly string[];
}

export interface FixReport {
  readonly entries: readonly FixEntry[];
  readonly totalRowsFixed: number;
}

type FixMode = "safe" | "moderate" | "aggressive";

/**
 * Apply fixes to data. Returns new TabularData + report.
 * Aggressive mode requires force=true.
 */
export function applyFixes(
  data: TabularData,
  _findings: readonly Finding[],
  mode: FixMode = "safe",
  force: boolean = false,
): { data: TabularData; report: FixReport } {
  if (mode === "aggressive" && !force) {
    throw new Error("Aggressive mode requires force=true");
  }

  const entries: FixEntry[] = [];
  let rows = data.rows.map((r) => ({ ...r }));

  for (const col of data.columns) {
    // Safe fixes (always run)
    if (mode === "safe" || mode === "moderate" || mode === "aggressive") {
      // Trim whitespace
      const trimResult = fixColumn(rows, col, (v) => {
        if (typeof v === "string" && v !== v.trim()) return v.trim();
        return v;
      });
      if (trimResult.count > 0) {
        entries.push({
          column: col,
          fixType: "trim_whitespace",
          rowsAffected: trimResult.count,
          sampleBefore: trimResult.before,
          sampleAfter: trimResult.after,
        });
        rows = trimResult.rows;
      }

      // Remove invisible characters (zero-width)
      const zwResult = fixColumn(rows, col, (v) => {
        if (typeof v === "string") {
          const cleaned = v.replace(/[\u200B\u200C\u200D\uFEFF]/g, "");
          if (cleaned !== v) return cleaned;
        }
        return v;
      });
      if (zwResult.count > 0) {
        entries.push({
          column: col,
          fixType: "remove_invisible_chars",
          rowsAffected: zwResult.count,
          sampleBefore: zwResult.before,
          sampleAfter: zwResult.after,
        });
        rows = zwResult.rows;
      }

      // Smart quote normalization
      const sqResult = fixColumn(rows, col, (v) => {
        if (typeof v === "string") {
          const fixed = v
            .replace(/[\u2018\u2019]/g, "'")
            .replace(/[\u201C\u201D]/g, '"');
          if (fixed !== v) return fixed;
        }
        return v;
      });
      if (sqResult.count > 0) {
        entries.push({
          column: col,
          fixType: "normalize_quotes",
          rowsAffected: sqResult.count,
          sampleBefore: sqResult.before,
          sampleAfter: sqResult.after,
        });
        rows = sqResult.rows;
      }
    }

    // Moderate fixes
    if (mode === "moderate" || mode === "aggressive") {
      // Strip control characters
      const ctrlResult = fixColumn(rows, col, (v) => {
        if (typeof v === "string") {
          const cleaned = v.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g, "");
          if (cleaned !== v) return cleaned;
        }
        return v;
      });
      if (ctrlResult.count > 0) {
        entries.push({
          column: col,
          fixType: "strip_control_chars",
          rowsAffected: ctrlResult.count,
          sampleBefore: ctrlResult.before,
          sampleAfter: ctrlResult.after,
        });
        rows = ctrlResult.rows;
      }
    }

    // Aggressive fixes
    if (mode === "aggressive") {
      // Coerce string to numeric
      const numResult = fixColumn(rows, col, (v) => {
        if (typeof v === "string") {
          const n = Number(v);
          if (Number.isFinite(n)) return n;
        }
        return v;
      });
      if (numResult.count > 0) {
        entries.push({
          column: col,
          fixType: "coerce_numeric",
          rowsAffected: numResult.count,
          sampleBefore: numResult.before,
          sampleAfter: numResult.after,
        });
        rows = numResult.rows;
      }
    }
  }

  const totalRowsFixed = entries.reduce((s, e) => s + e.rowsAffected, 0);

  return {
    data: new TabularData(rows),
    report: { entries, totalRowsFixed },
  };
}

function fixColumn(
  rows: Record<string, unknown>[],
  col: string,
  fn: (v: unknown) => unknown,
): { rows: Record<string, unknown>[]; count: number; before: string[]; after: string[] } {
  let count = 0;
  const before: string[] = [];
  const after: string[] = [];
  const newRows = rows.map((r) => {
    const v = r[col];
    if (isNullish(v)) return r;
    const fixed = fn(v);
    if (fixed !== v) {
      count++;
      if (before.length < 5) {
        before.push(String(v));
        after.push(String(fixed));
      }
      return { ...r, [col]: fixed };
    }
    return r;
  });
  return { rows: newRows, count, before, after };
}
