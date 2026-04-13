/**
 * Constraint mining: functional dependencies, candidate keys, temporal orders.
 * TypeScript port of goldencheck/baseline/constraints.py.
 * Edge-safe: no Node.js dependencies.
 */

import { TabularData, isNullish } from "../data.js";
import type { FunctionalDependency, TemporalOrder } from "./models.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Maximum number of columns to consider for FD mining. */
const MAX_COLS = 30;

/** Maximum unique values for a column to be treated as low-cardinality in FD mining. */
const MAX_UNIQUE = 1000;

/** Minimum rows required before mining. */
const MIN_ROWS = 30;

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface ConstraintResult {
  functionalDeps: FunctionalDependency[];
  candidateKeys: string[];
  temporalOrders: TemporalOrder[];
}

/**
 * Mine structural constraints from the data.
 *
 * @param data - Input TabularData.
 * @param minConfidence - Minimum confidence for functional dependencies (default 0.95).
 * @param dateColumns - Optional list of date column names for temporal order mining.
 */
export function mineConstraints(
  data: TabularData,
  minConfidence: number = 0.95,
  dateColumns: string[] = [],
): ConstraintResult {
  if (data.rowCount < MIN_ROWS) {
    return { functionalDeps: [], candidateKeys: [], temporalOrders: [] };
  }

  const functionalDeps = mineFunctionalDependencies(data, minConfidence);
  const candidateKeys = mineCandidateKeys(data);
  const temporalOrders = mineTemporalOrders(data, dateColumns);

  return { functionalDeps, candidateKeys, temporalOrders };
}

// ---------------------------------------------------------------------------
// Functional dependency mining (simplified TANE — single-column determinants)
// ---------------------------------------------------------------------------

function mineFunctionalDependencies(
  data: TabularData,
  minConfidence: number,
): FunctionalDependency[] {
  const nRows = data.rowCount;

  // Select lowest-cardinality columns, up to MAX_COLS
  const cardinalities: Array<[string, number]> = [];
  for (const col of data.columns) {
    const nUnique = data.nUnique(col);
    if (nUnique < MAX_UNIQUE) {
      cardinalities.push([col, nUnique]);
    }
  }
  cardinalities.sort((a, b) => a[1] - b[1]);
  const candidateCols = cardinalities.slice(0, MAX_COLS).map(([col]) => col);

  // Accumulate: determinant -> { dependent -> maxConfidence }
  const detToDeps = new Map<string, Map<string, number>>();

  for (const det of candidateCols) {
    for (const dep of candidateCols) {
      if (det === dep) continue;

      // Group by determinant, check if dependent is consistent.
      // Confidence = fraction of rows matching the most frequent dependent value per group.
      const groups = new Map<string, Map<string, number>>();

      for (const row of data.rows) {
        const detVal = String(row[det] ?? "__null__");
        const depVal = String(row[dep] ?? "__null__");

        let depMap = groups.get(detVal);
        if (!depMap) {
          depMap = new Map<string, number>();
          groups.set(detVal, depMap);
        }
        depMap.set(depVal, (depMap.get(depVal) ?? 0) + 1);
      }

      // Sum the max count per determinant group
      let consistentCount = 0;
      for (const depMap of groups.values()) {
        let maxCount = 0;
        for (const count of depMap.values()) {
          if (count > maxCount) maxCount = count;
        }
        consistentCount += maxCount;
      }

      const confidence = consistentCount / nRows;
      if (confidence >= minConfidence) {
        let depMap = detToDeps.get(det);
        if (!depMap) {
          depMap = new Map<string, number>();
          detToDeps.set(det, depMap);
        }
        const prev = depMap.get(dep) ?? 0;
        if (confidence > prev) {
          depMap.set(dep, confidence);
        }
      }
    }
  }

  // Merge: for each determinant, combine all dependents into one FD.
  // Use the minimum confidence across dependents (most conservative).
  const fds: FunctionalDependency[] = [];
  for (const [det, depMap] of detToDeps) {
    if (depMap.size === 0) continue;
    const dependents = [...depMap.keys()].sort();
    let minConf = Infinity;
    for (const conf of depMap.values()) {
      if (conf < minConf) minConf = conf;
    }
    // Emit one FD per dependent to match the interface (single determinant -> single dependent)
    for (const dep of dependents) {
      fds.push({
        determinant: det,
        dependent: dep,
        confidence: depMap.get(dep)!,
      });
    }
  }

  return fds;
}

// ---------------------------------------------------------------------------
// Candidate key detection
// ---------------------------------------------------------------------------

function mineCandidateKeys(data: TabularData): string[] {
  const nRows = data.rowCount;
  const keys: string[] = [];

  for (const col of data.columns) {
    const nullCount = data.nullCount(col);
    if (nullCount > 0) continue;
    const nUnique = data.nUnique(col);
    if (nUnique === nRows) {
      keys.push(col);
    }
  }

  return keys;
}

// ---------------------------------------------------------------------------
// Temporal order mining
// ---------------------------------------------------------------------------

function mineTemporalOrders(
  data: TabularData,
  dateColumns: string[],
): TemporalOrder[] {
  const present = dateColumns.filter((c) => data.columns.includes(c));
  if (present.length < 2) return [];

  const orders: TemporalOrder[] = [];

  for (let i = 0; i < present.length; i++) {
    const colA = present[i]!;
    for (let j = i + 1; j < present.length; j++) {
      const colB = present[j]!;

      // Compute violation rate: rows where colA > colB
      let validPairs = 0;
      let violationsAB = 0;

      for (const row of data.rows) {
        const aVal = row[colA];
        const bVal = row[colB];

        if (isNullish(aVal) || isNullish(bVal)) continue;

        const aStr = String(aVal);
        const bStr = String(bVal);
        const aDate = new Date(aStr);
        const bDate = new Date(bStr);

        if (isNaN(aDate.getTime()) || isNaN(bDate.getTime())) continue;

        validPairs++;
        if (aDate > bDate) violationsAB++;
      }

      if (validPairs === 0) continue;

      const violationRate = violationsAB / validPairs;

      if (violationRate < 0.5) {
        // colA is naturally before colB
        orders.push({
          startCol: colA,
          endCol: colB,
          violationRate,
        });
      } else {
        // Majority suggests colB before colA
        orders.push({
          startCol: colB,
          endCol: colA,
          violationRate: 1.0 - violationRate,
        });
      }
    }
  }

  return orders;
}
