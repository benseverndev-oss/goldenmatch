// Vendored Hungarian / Kuhn-Munkres optimal assignment.
//
// We MINIMIZE a cost matrix. Callers that have a score matrix (higher = better)
// should pass cost = 1 - score, matching the Python infermap convention.
//
// Supports rectangular matrices by padding with a large cost. The returned
// assignment drops any rows/cols whose match touches a padded slot.
//
// Complexity: O(n^3) where n = max(rows, cols).
//
// Deterministic tie-breaking: the algorithm iterates rows/cols in index order,
// so when multiple optimal assignments exist the lexicographically smallest
// (by (row, col)) is preferred. The Python side rounds scores before assignment
// to increase the chance of matching scipy's choice on fixtures.

export interface AssignmentPair {
  row: number;
  col: number;
  cost: number;
}

/**
 * Solve the rectangular linear sum assignment problem.
 * Returns one pair per min(rows, cols), minimizing total cost.
 */
export function linearSumAssignment(costMatrix: number[][]): AssignmentPair[] {
  const rows = costMatrix.length;
  if (rows === 0) return [];
  const cols = costMatrix[0]!.length;
  if (cols === 0) return [];

  const n = Math.max(rows, cols);
  // Big-M must dominate any real assignment (so padded slots are only taken
  // when forced by the rectangular shape) but stay small enough to preserve
  // double precision during potential updates. 1e18 is too large: 1e18 + 10
  // rounds back to 1e18 and breaks the algorithm. Compute from input scale.
  let maxAbs = 0;
  for (let i = 0; i < rows; i++) {
    const row = costMatrix[i]!;
    for (let j = 0; j < cols; j++) {
      const v = row[j]!;
      if (Number.isFinite(v)) {
        const a = Math.abs(v);
        if (a > maxAbs) maxAbs = a;
      }
    }
  }
  const INF = (maxAbs + 1) * (n + 1) * 4 + 1;

  const c: number[][] = Array.from({ length: n }, (_, i) =>
    Array.from({ length: n }, (_, j) => {
      if (i < rows && j < cols) {
        const row = costMatrix[i]!;
        const v = row[j]!;
        return Number.isFinite(v) ? v : INF;
      }
      return INF;
    })
  );

  // Standard O(n^3) Hungarian with potentials (Kuhn-Munkres, "Jonker-Volgenant-lite").
  // Reference: competitive-programming handbooks; this is the shortest-path variant.
  const u = new Array<number>(n + 1).fill(0);
  const v = new Array<number>(n + 1).fill(0);
  const p = new Array<number>(n + 1).fill(0); // p[j] = row assigned to col j (1-indexed)
  const way = new Array<number>(n + 1).fill(0);

  for (let i = 1; i <= n; i++) {
    p[0] = i;
    let j0 = 0;
    const minv = new Array<number>(n + 1).fill(Infinity);
    const used = new Array<boolean>(n + 1).fill(false);
    do {
      used[j0] = true;
      const i0 = p[j0]!;
      let delta = Infinity;
      let j1 = 0;
      for (let j = 1; j <= n; j++) {
        if (!used[j]) {
          const cur = c[i0 - 1]![j - 1]! - u[i0]! - v[j]!;
          if (cur < minv[j]!) {
            minv[j] = cur;
            way[j] = j0;
          }
          if (minv[j]! < delta) {
            delta = minv[j]!;
            j1 = j;
          }
        }
      }
      for (let j = 0; j <= n; j++) {
        if (used[j]) {
          u[p[j]!] = u[p[j]!]! + delta;
          v[j] = v[j]! - delta;
        } else {
          minv[j] = minv[j]! - delta;
        }
      }
      j0 = j1;
    } while (p[j0] !== 0);
    do {
      const j1 = way[j0]!;
      p[j0] = p[j1]!;
      j0 = j1;
    } while (j0 !== 0);
  }

  // p[j] = row assigned to col j (1-indexed, 0 = dummy)
  const pairs: AssignmentPair[] = [];
  for (let j = 1; j <= n; j++) {
    const i = p[j]! - 1;
    const jj = j - 1;
    if (i >= 0 && i < rows && jj < cols) {
      const cost = costMatrix[i]![jj]!;
      if (Number.isFinite(cost)) {
        pairs.push({ row: i, col: jj, cost });
      }
    }
  }
  pairs.sort((a, b) => a.row - b.row || a.col - b.col);
  return pairs;
}

/**
 * Convenience: optimal assignment on a score matrix (higher = better),
 * matching Python infermap.assignment.optimal_assign. Filters by min_confidence
 * and rounds scores to 4 decimal places for parity with scipy output.
 */
export interface ScoreAssignment {
  sourceIdx: number;
  targetIdx: number;
  score: number;
}

export function optimalAssign(
  scoreMatrix: number[][],
  minConfidence = 0.3
): ScoreAssignment[] {
  if (scoreMatrix.length === 0) return [];
  const cols = scoreMatrix[0]?.length ?? 0;
  if (cols === 0) return [];

  const costMatrix = scoreMatrix.map((row) => row.map((s) => 1 - s));
  const pairs = linearSumAssignment(costMatrix);
  const out: ScoreAssignment[] = [];
  for (const { row, col } of pairs) {
    const score = scoreMatrix[row]![col]!;
    if (score >= minConfidence) {
      out.push({
        sourceIdx: row,
        targetIdx: col,
        score: Math.round(score * 10000) / 10000,
      });
    }
  }
  return out;
}
