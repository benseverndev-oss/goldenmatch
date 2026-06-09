import { describe, expect, it } from "vitest";
import {
  columns,
  duplicateRowRatio,
  histogram,
  nUnique,
  nullRatioPerColumn,
  quantile,
} from "../../src/core/aggregate.js";

describe("aggregate", () => {
  it("nullRatioPerColumn", () => {
    const rows = [{ a: 1, b: 1 }, { a: 2, b: 2 }, { a: null, b: 3 }, { a: null, b: 4 }, { a: 5, b: 5 }];
    expect(nullRatioPerColumn(rows)).toEqual({ a: 0.4, b: 0 });
  });

  it("duplicateRowRatio: one identical pair in five => 0.4", () => {
    const rows = [{ a: 1, b: "x" }, { a: 1, b: "x" }, { a: 2, b: "y" }, { a: 3, b: "z" }, { a: 4, b: "w" }];
    expect(duplicateRowRatio(rows)).toBe(0.4);
  });

  it("duplicateRowRatio: no dupes", () => {
    expect(duplicateRowRatio([{ a: 1 }, { a: 2 }, { a: 3 }])).toBe(0);
  });

  it("histogram equal-width", () => {
    expect(histogram([1, 2, 3, 4], 2)).toEqual([[1, 2], [2.5, 2]]);
  });

  it("histogram single value", () => {
    expect(histogram([7, 7, 7], 4)).toEqual([[7, 3]]);
  });

  it("quantile linear", () => {
    expect(quantile([1, 2, 3, 4], 0.5)).toBe(2.5);
    expect(quantile([1, 2, 3, 4], 0)).toBe(1);
    expect(quantile([1, 2, 3, 4], 1)).toBe(4);
  });

  it("columns + nUnique", () => {
    const rows = [{ a: 1, b: "x" }, { a: 1, c: "y" }];
    expect(columns(rows)).toEqual(["a", "b", "c"]);
    expect(nUnique(rows, "a")).toBe(1);
  });
});
