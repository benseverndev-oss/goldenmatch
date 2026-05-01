import { describe, it, expect } from "vitest";
import { loadDomain, listDomains } from "../src/loader.js";

describe("loader", () => {
  it("listDomains includes finance, healthcare, ecommerce, generic", () => {
    const d = listDomains();
    expect(d).toContain("finance");
    expect(d).toContain("healthcare");
    expect(d).toContain("ecommerce");
    expect(d).toContain("generic");
  });

  it("loads finance pack", () => {
    const pack = loadDomain("finance");
    expect(pack.name).toBe("finance");
    expect(pack.types["account_number"]).toBeDefined();
  });

  it("throws on unknown pack", () => {
    expect(() => loadDomain("does_not_exist")).toThrow();
  });

  it("generic pack is empty", () => {
    const pack = loadDomain("generic");
    expect(pack.types).toEqual({});
  });
});
