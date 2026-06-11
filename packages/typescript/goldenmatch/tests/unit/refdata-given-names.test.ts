import { describe, it, expect } from "vitest";
import { areEquivalent, isAvailable } from "../../src/core/refdata/givenNames.js";

describe("givenNames.isAvailable", () => {
  it("is true (alias table is bundled)", () => {
    expect(isAvailable()).toBe(true);
  });
});

describe("givenNames.areEquivalent", () => {
  it("known cross-form pairs, symmetric", () => {
    expect(areEquivalent("Robert", "Bob")).toBe(true);
    expect(areEquivalent("Bob", "Robert")).toBe(true);
    expect(areEquivalent("William", "Bill")).toBe(true);
  });
  it("equivalent within a class", () => {
    expect(areEquivalent("Bob", "Rob")).toBe(true);
  });
  it("reflexive, incl. OOV identical-after-normalize", () => {
    expect(areEquivalent("Robert", "Robert")).toBe(true);
    expect(areEquivalent("zorkian", "Zorkian")).toBe(true);
    expect(areEquivalent("B.o.b", "bob")).toBe(true); // non-alpha stripped
  });
  it("distinct names are not equivalent", () => {
    expect(areEquivalent("Robert", "William")).toBe(false);
    expect(areEquivalent("Bob", "Bill")).toBe(false);
  });
  it("OOV pair (both absent) is not equivalent", () => {
    expect(areEquivalent("Zorkwhibblefnord", "Quibblefnord")).toBe(false);
  });
  it("null / empty inputs are not equivalent", () => {
    expect(areEquivalent(null, "Robert")).toBe(false);
    expect(areEquivalent("Robert", null)).toBe(false);
    expect(areEquivalent(null, null)).toBe(false);
    expect(areEquivalent("", "Robert")).toBe(false);
  });
});
