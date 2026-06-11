import { describe, it, expect } from "vitest";
import { isAvailable, surnameRank, surnameIdf } from "../../src/core/refdata/surnames.js";

describe("surnames.isAvailable", () => {
  it("is true (census table bundled)", () => {
    expect(isAvailable()).toBe(true);
  });
});

describe("surnames.surnameRank", () => {
  it("Smith is rank 1", () => {
    expect(surnameRank("Smith")).toBe(1);
  });
  it("case-insensitive", () => {
    expect(surnameRank("smith")).toBe(surnameRank("SMITH"));
    expect(surnameRank("SmIth")).toBe(1);
  });
  it("strips non-alpha", () => {
    expect(surnameRank("O'Brien")).toBe(surnameRank("OBRIEN"));
    expect(surnameRank("  Smith  ")).toBe(1);
  });
  it("unknown name -> null", () => {
    expect(surnameRank("Zorkwhibblefnord")).toBeNull();
  });
  it("null -> null", () => {
    expect(surnameRank(null)).toBeNull();
  });
});

describe("surnames.surnameIdf", () => {
  it("OOV name -> 1.0 (rarer than the table)", () => {
    expect(surnameIdf("Zorkwhibblefnord")).toBe(1.0);
  });
  it("common name (Smith) idf < 0.45", () => {
    const idf = surnameIdf("Smith");
    expect(idf).not.toBeNull();
    expect(idf!).toBeLessThan(0.45);
  });
  it("non-decreasing with rank (Smith <= Johnson <= Williams)", () => {
    const s = surnameIdf("Smith")!;
    const j = surnameIdf("Johnson")!;
    const w = surnameIdf("Williams")!;
    expect(s).toBeLessThanOrEqual(j);
    expect(j).toBeLessThanOrEqual(w);
  });
  it("null -> null", () => {
    expect(surnameIdf(null)).toBeNull();
  });
});
