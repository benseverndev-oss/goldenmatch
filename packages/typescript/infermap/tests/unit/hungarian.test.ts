import { describe, it, expect } from "vitest";
import {
  linearSumAssignment,
  optimalAssign,
} from "../../src/core/assignment/hungarian.js";

const totalCost = (
  matrix: number[][],
  pairs: Array<{ row: number; col: number }>
): number => pairs.reduce((sum, p) => sum + matrix[p.row]![p.col]!, 0);

describe("linearSumAssignment", () => {
  it("handles empty matrix", () => {
    expect(linearSumAssignment([])).toEqual([]);
    expect(linearSumAssignment([[]])).toEqual([]);
  });

  it("solves a 2x2 min-cost assignment", () => {
    // Optimal: (0,1)+(1,0) = 1 + 2 = 3; alternative (0,0)+(1,1) = 4 + 3 = 7
    const cost = [
      [4, 1],
      [2, 3],
    ];
    const pairs = linearSumAssignment(cost);
    expect(pairs).toHaveLength(2);
    expect(totalCost(cost, pairs)).toBe(3);
  });

  it("solves a 3x3 known case", () => {
    // Optimal: (0,0)=10 + (1,2)=7 + (2,1)=16 = 33
    const cost = [
      [10, 19, 8],
      [10, 18, 7],
      [13, 16, 9],
    ];
    const pairs = linearSumAssignment(cost);
    expect(pairs).toHaveLength(3);
    expect(totalCost(cost, pairs)).toBe(33);
  });

  it("handles rectangular wide matrix (more cols than rows)", () => {
    // 2 rows, 3 cols — pick the 2 cheapest disjoint assignments
    const cost = [
      [5, 1, 9],
      [9, 9, 2],
    ];
    const pairs = linearSumAssignment(cost);
    expect(pairs).toHaveLength(2);
    // Best: (0,1)=1 + (1,2)=2 = 3
    expect(totalCost(cost, pairs)).toBe(3);
  });

  it("handles rectangular tall matrix (more rows than cols)", () => {
    const cost = [
      [5, 1],
      [9, 9],
      [2, 4],
    ];
    const pairs = linearSumAssignment(cost);
    expect(pairs).toHaveLength(2);
    // Best: (0,1)=1 + (2,0)=2 = 3
    expect(totalCost(cost, pairs)).toBe(3);
    // Row 1 should be dropped (padded)
    expect(pairs.find((p) => p.row === 1)).toBeUndefined();
  });

  it("returns pairs sorted by (row, col)", () => {
    const cost = [
      [0, 1, 2],
      [1, 0, 2],
      [2, 1, 0],
    ];
    const pairs = linearSumAssignment(cost);
    for (let i = 1; i < pairs.length; i++) {
      expect(pairs[i]!.row).toBeGreaterThanOrEqual(pairs[i - 1]!.row);
    }
  });
});

describe("optimalAssign (score-matrix wrapper)", () => {
  it("returns empty on empty input", () => {
    expect(optimalAssign([])).toEqual([]);
    expect(optimalAssign([[]])).toEqual([]);
  });

  it("filters by minConfidence", () => {
    // Scores so low that all pairs fall below threshold
    const scores = [
      [0.1, 0.2],
      [0.15, 0.05],
    ];
    expect(optimalAssign(scores, 0.3)).toEqual([]);
  });

  it("keeps pairs above threshold and rounds score", () => {
    const scores = [
      [0.9, 0.1],
      [0.1, 0.85],
    ];
    const out = optimalAssign(scores, 0.3);
    expect(out).toEqual([
      { sourceIdx: 0, targetIdx: 0, score: 0.9 },
      { sourceIdx: 1, targetIdx: 1, score: 0.85 },
    ]);
  });

  it("prefers higher-score pairings", () => {
    const scores = [
      [0.3, 0.9],
      [0.95, 0.4],
    ];
    const out = optimalAssign(scores, 0.3);
    // Optimal: (0,1)=0.9 + (1,0)=0.95 = 1.85 > (0,0)+(1,1)=0.7
    expect(out).toHaveLength(2);
    const sum = out.reduce((s, p) => s + p.score, 0);
    expect(sum).toBeCloseTo(1.85, 4);
  });
});
