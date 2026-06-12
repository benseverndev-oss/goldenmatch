/**
 * Approximate / exact duplicate-row detection (cross-column relation).
 * Port of goldencheck/relations/approx_duplicate.py (pure-Polars → pure-JS;
 * no native kernel by design).
 *
 * - duplicate_rows: byte-identical rows.
 * - near_duplicate_rows: rows identical after lowercasing, collapsing
 *   whitespace, and dropping punctuation on string columns — and that have NO
 *   exact twin.
 */
import type { TabularData } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { RelationProfiler } from "../profilers/base.js";

const SEP = String.fromCharCode(31); // unit separator — won't appear in normal data

/** Per-row signature; string columns normalized, others stringified as-is. */
function signatures(data: TabularData, normalizeStrings: boolean): string[] {
  const stringCols = new Set(data.columns.filter((c) => data.dtype(c) === "string"));
  const colVals = data.columns.map((c) => data.column(c));
  const n = data.rowCount;
  const out: string[] = new Array(n);
  for (let i = 0; i < n; i++) {
    const parts: string[] = [];
    for (let c = 0; c < data.columns.length; c++) {
      const raw = colVals[c]![i];
      let s = raw === null ? "" : String(raw);
      if (normalizeStrings && stringCols.has(data.columns[c]!)) {
        s = s.toLowerCase().replace(/[^0-9a-z]+/g, " ").trim();
      }
      parts.push(s);
    }
    out[i] = parts.join(SEP);
  }
  return out;
}

export class ApproxDuplicateProfiler implements RelationProfiler {
  profile(data: TabularData): Finding[] {
    const nRows = data.rowCount;
    if (nRows < 2 || data.columns.length === 0) return [];

    const norm = signatures(data, true);
    const exact = signatures(data, false);

    const normCounts = new Map<string, number>();
    const exactCounts = new Map<string, number>();
    for (let i = 0; i < nRows; i++) {
      normCounts.set(norm[i]!, (normCounts.get(norm[i]!) ?? 0) + 1);
      exactCounts.set(exact[i]!, (exactCounts.get(exact[i]!) ?? 0) + 1);
    }

    const findings: Finding[] = [];

    // Exact duplicate rows.
    let exactDupRows = 0;
    const exactDupGroups = new Set<string>();
    for (let i = 0; i < nRows; i++) {
      if ((exactCounts.get(exact[i]!) ?? 0) >= 2) {
        exactDupRows++;
        exactDupGroups.add(exact[i]!);
      }
    }
    if (exactDupRows > 0) {
      const g = exactDupGroups.size;
      findings.push(
        makeFinding({
          severity: Severity.WARNING,
          column: "__dataset__",
          check: "duplicate_rows",
          message:
            `${exactDupRows} rows are exact duplicates ` +
            `(${g} distinct duplicated record${g !== 1 ? "s" : ""}).`,
          affectedRows: exactDupRows,
          suggestion:
            "De-duplicate before downstream processing, or confirm the " +
            "repetition is intentional (e.g. a denormalized fact table).",
          confidence: 0.7,
          metadata: { technique: "duplicate_rows", duplicate_groups: g },
        }),
      );
    }

    // Near-duplicates: share a normalized signature but have no exact twin.
    let nearDupRows = 0;
    const nearDupGroups = new Set<string>();
    for (let i = 0; i < nRows; i++) {
      if ((normCounts.get(norm[i]!) ?? 0) >= 2 && (exactCounts.get(exact[i]!) ?? 0) < 2) {
        nearDupRows++;
        nearDupGroups.add(norm[i]!);
      }
    }
    if (nearDupRows > 0) {
      const g = nearDupGroups.size;
      findings.push(
        makeFinding({
          severity: Severity.WARNING,
          column: "__dataset__",
          check: "near_duplicate_rows",
          message:
            `${nearDupRows} rows are near-duplicates — identical after ` +
            `lowercasing, collapsing whitespace, and removing punctuation ` +
            `(${g} group${g !== 1 ? "s" : ""}).`,
          affectedRows: nearDupRows,
          suggestion:
            "Standardize casing/whitespace/punctuation (or run an entity-" +
            "resolution pass) so these records reconcile to one.",
          confidence: 0.6,
          metadata: { technique: "near_duplicate_rows", near_duplicate_groups: g },
        }),
      );
    }

    return findings;
  }
}
