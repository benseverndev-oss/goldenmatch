/**
 * Referential-integrity checks across TWO tables (foreign-key validation).
 *
 * GoldenCheck's scan path is single-file; this fills the common cross-table gap:
 * do a child table's foreign-key values all exist in the parent's key? E.g.
 * `orders.customer_id` must be a subset of `customers.id`. Reports orphan rows
 * (FK values with no parent match), the orphan rate, and the join cardinality.
 *
 * Edge-safe / pure: operates on already-read `TabularData` (set membership over the
 * parent key set + a few counts — no native kernel, no `node:fs`). File reading is
 * the CLI action's job, mirroring how `diff` reads both files in the node layer.
 *
 * Parity with `packages/python/goldencheck/goldencheck/engine/referential.py`.
 */

import type { TabularData, ColumnValue } from "../data.js";
import { type Finding, makeFinding, Severity } from "../types.js";

// Orphan rate above this => ERROR; any orphans below it => WARNING.
const ERROR_ORPHAN_RATE = 0.01;
const MAX_SAMPLE = 5;

/** A column usable as a parent key: present, non-null, and fully unique. */
function isKey(data: TabularData, col: string): boolean {
  if (!data.columns.includes(col)) return false;
  const values = data.column(col);
  const n = values.length;
  if (n === 0) return false;
  const nulls = values.reduce<number>((acc, v) => acc + (v === null ? 1 : 0), 0);
  return nulls === 0 && data.nUnique(col) === n;
}

/** Same-named columns that are a unique+non-null key on the parent side. */
export function autoDetectMappings(child: TabularData, parent: TabularData): Array<[string, string]> {
  const mappings: Array<[string, string]> = [];
  for (const col of child.columns) {
    if (parent.columns.includes(col) && isKey(parent, col)) {
      mappings.push([col, col]);
    }
  }
  return mappings;
}

/** Parse `--on` specs: 'child=parent' or 'col' (same name both sides). */
export function parseOn(spec: readonly string[]): Array<[string, string]> {
  const out: Array<[string, string]> = [];
  for (const s of spec) {
    const eq = s.indexOf("=");
    if (eq >= 0) {
      out.push([s.slice(0, eq).trim(), s.slice(eq + 1).trim()]);
    } else {
      out.push([s.trim(), s.trim()]);
    }
  }
  return out;
}

export interface ReferentialOptions {
  readonly childName?: string;
  readonly parentName?: string;
}

/** For each (child_fk, parent_key) mapping, find orphan FK values. */
export function checkReferentialIntegrity(
  child: TabularData,
  parent: TabularData,
  mappings: ReadonlyArray<readonly [string, string]>,
  opts: ReferentialOptions = {},
): Finding[] {
  const childName = opts.childName ?? "child";
  const parentName = opts.parentName ?? "parent";
  const findings: Finding[] = [];

  for (const [childCol, parentCol] of mappings) {
    if (!child.columns.includes(childCol) || !parent.columns.includes(parentCol)) {
      findings.push(makeFinding({
        severity: Severity.WARNING,
        column: childCol,
        check: "referential_integrity",
        message: `Cannot check '${childName}.${childCol}' -> '${parentName}.${parentCol}': column not found.`,
        confidence: 0.9,
        metadata: { technique: "referential_integrity" },
      }));
      continue;
    }

    const childFk = child.column(childCol);
    const nonNull = childFk.filter((v): v is Exclude<ColumnValue, null> => v !== null);
    const considered = nonNull.length;
    if (considered === 0) continue;

    const parentKeys = new Set<ColumnValue>(parent.column(parentCol).filter((v) => v !== null));
    const orphans = nonNull.filter((v) => !parentKeys.has(v));
    const orphanRows = orphans.length;

    // Join cardinality (informational): is each side unique on the key?
    const childUnique = child.nUnique(childCol) === childFk.length;
    const parentUnique = isKey(parent, parentCol);
    const cardinality = `${childUnique ? "1" : "N"}:${parentUnique ? "1" : "N"}`;

    if (orphanRows === 0) {
      findings.push(makeFinding({
        severity: Severity.INFO,
        column: childCol,
        check: "referential_integrity",
        message: `'${childName}.${childCol}' fully references '${parentName}.${parentCol}' (${considered} rows, ${cardinality}).`,
        affectedRows: 0,
        confidence: 0.9,
        metadata: {
          technique: "referential_integrity",
          child_column: childCol,
          parent_column: parentCol,
          cardinality,
          orphan_rows: 0,
        },
      }));
      continue;
    }

    const distinctOrphanValues = [...new Set(orphans)];
    const distinctOrphans = distinctOrphanValues.length;
    const rate = orphanRows / considered;
    const severity = rate > ERROR_ORPHAN_RATE ? Severity.ERROR : Severity.WARNING;
    const samples = distinctOrphanValues.slice(0, MAX_SAMPLE).map((v) => String(v));

    findings.push(makeFinding({
      severity,
      column: childCol,
      check: "referential_integrity",
      message:
        `${orphanRows} row(s) (${distinctOrphans} distinct value(s), ${formatPct(rate)}) in ` +
        `'${childName}.${childCol}' have no match in '${parentName}.${parentCol}' — orphaned foreign keys.`,
      affectedRows: orphanRows,
      sampleValues: samples,
      suggestion:
        `Backfill the missing '${parentName}.${parentCol}' rows, or treat these ` +
        `orphaned '${childCol}' values as invalid.`,
      confidence: 0.85,
      metadata: {
        technique: "referential_integrity",
        child_column: childCol,
        parent_column: parentCol,
        cardinality,
        orphan_rows: orphanRows,
        distinct_orphans: distinctOrphans,
        orphan_rate: Number(rate.toFixed(6)),
      },
    }));
  }

  return findings;
}

/**
 * Orchestrator: check referential integrity between two already-read tables.
 * When `on` is omitted, auto-detect FK mappings from same-named parent-key columns;
 * emit a guidance INFO finding when nothing can be inferred.
 */
export function referentialIntegrity(
  child: TabularData,
  parent: TabularData,
  on: readonly string[] | undefined,
  opts: ReferentialOptions = {},
): Finding[] {
  const mappings = on && on.length > 0 ? parseOn(on) : autoDetectMappings(child, parent);
  if (mappings.length === 0) {
    return [makeFinding({
      severity: Severity.INFO,
      column: "__dataset__",
      check: "referential_integrity",
      message:
        "No foreign-key relationship detected (no shared unique-key column). " +
        "Pass --on child_col=parent_col to check explicitly.",
      confidence: 0.5,
      metadata: { technique: "referential_integrity" },
    })];
  }
  return checkReferentialIntegrity(child, parent, mappings, opts);
}

/** Mirror Python's `{rate:.1%}` formatting (one decimal, e.g. 5.0%). */
function formatPct(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}
