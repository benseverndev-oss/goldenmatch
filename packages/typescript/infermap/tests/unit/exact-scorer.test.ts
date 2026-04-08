import { describe, it, expect } from "vitest";
import { ExactScorer } from "../../src/core/scorers/exact.js";
import { makeFieldInfo } from "../../src/core/types.js";

describe("ExactScorer", () => {
  const scorer = new ExactScorer();

  it("declares its identity", () => {
    expect(scorer.name).toBe("ExactScorer");
    expect(scorer.weight).toBe(1.0);
  });

  it("scores 1.0 on identical names", () => {
    const r = scorer.score(
      makeFieldInfo({ name: "customer_id" }),
      makeFieldInfo({ name: "customer_id" })
    );
    expect(r.score).toBe(1);
    expect(r.reasoning).toContain("customer_id");
  });

  it("is case-insensitive and trim-insensitive", () => {
    const r = scorer.score(
      makeFieldInfo({ name: "  Customer_ID  " }),
      makeFieldInfo({ name: "customer_id" })
    );
    expect(r.score).toBe(1);
  });

  it("scores 0.0 on mismatch", () => {
    const r = scorer.score(
      makeFieldInfo({ name: "customer_id" }),
      makeFieldInfo({ name: "user_id" })
    );
    expect(r.score).toBe(0);
    expect(r.reasoning).toContain("No exact match");
  });
});
