import { describe, it, expect } from "vitest";
import { Severity, makeFinding, TabularData, healthScore } from "../src/core/index.js";

describe("smoke", () => {
  it("exports core types", () => {
    expect(Severity.ERROR).toBe(3);
    expect(typeof makeFinding).toBe("function");
    expect(typeof TabularData).toBe("function");
    expect(typeof healthScore).toBe("function");
  });

  it("creates TabularData from records", () => {
    const data = new TabularData([
      { a: 1, b: "hello" },
      { a: 2, b: "world" },
    ]);
    expect(data.rowCount).toBe(2);
    expect(data.columns).toEqual(["a", "b"]);
  });
});
