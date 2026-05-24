/**
 * Unit tests for the columnContext heuristics: name regex, IQR cardinality
 * banding, identifier inference, and flow enrichment.
 */

import { describe, it, expect } from "vitest";
import {
  ColumnType,
  CardinalityBand,
  classifyByName,
  normalizeDtype,
  buildContextsFromCheck,
  enrichContextsFromFlow,
  makeColumnContext,
  distinctNonNull,
  nullRateOf,
  type ColumnProfileLike,
} from "../../src/core/index.js";

describe("classifyByName", () => {
  it("matches name/email/phone/date/geo/zip/address/identifier patterns", () => {
    expect(classifyByName("first_name")).toBe(ColumnType.NAME);
    expect(classifyByName("surname")).toBe(ColumnType.NAME);
    expect(classifyByName("email_addr")).toBe(ColumnType.EMAIL);
    expect(classifyByName("mobile")).toBe(ColumnType.PHONE);
    expect(classifyByName("signup_date")).toBe(ColumnType.DATE);
    expect(classifyByName("dob")).toBe(ColumnType.DATE);
    expect(classifyByName("state")).toBe(ColumnType.GEO);
    expect(classifyByName("zip_code")).toBe(ColumnType.ZIP);
    expect(classifyByName("street")).toBe(ColumnType.ADDRESS);
    expect(classifyByName("customer_id")).toBe(ColumnType.IDENTIFIER);
    expect(classifyByName("random_label")).toBeNull();
  });

  it("date pattern wins over name (matches Python ordering)", () => {
    // "_date$" is checked before name patterns in the Python port.
    expect(classifyByName("birth_date")).toBe(ColumnType.DATE);
  });
});

describe("normalizeDtype", () => {
  it("maps polars-ish dtypes to ColumnType", () => {
    expect(normalizeDtype("Int64")).toBe(ColumnType.NUMERIC);
    expect(normalizeDtype("Float32")).toBe(ColumnType.NUMERIC);
    expect(normalizeDtype("Datetime")).toBe(ColumnType.DATE);
    expect(normalizeDtype("Boolean")).toBe(ColumnType.STRING);
    expect(normalizeDtype("String")).toBe(ColumnType.STRING);
  });
});

describe("makeColumnContext validation", () => {
  it("rejects bad invariants", () => {
    expect(() => makeColumnContext({ name: "" })).toThrow(/non-empty/);
    expect(() => makeColumnContext({ name: "x", nullRate: 2 })).toThrow(/nullRate/);
    expect(() => makeColumnContext({ name: "x", cardinality: -1 })).toThrow(/cardinality/);
    expect(() => makeColumnContext({ name: "x", confidence: -0.1 })).toThrow(/confidence/);
  });
});

describe("buildContextsFromCheck", () => {
  it("returns [] when no profiles", () => {
    expect(buildContextsFromCheck([], null)).toEqual([]);
    expect(buildContextsFromCheck([], [])).toEqual([]);
  });

  it("classifies columns and bands cardinality via IQR", () => {
    const profiles: ColumnProfileLike[] = [
      { name: "first_name", inferredType: "String", uniqueCount: 50, nullPct: 0 },
      { name: "last_name", inferredType: "String", uniqueCount: 60, nullPct: 0 },
      { name: "email", inferredType: "String", uniqueCount: 100, nullPct: 0 },
      { name: "status", inferredType: "String", uniqueCount: 3, nullPct: 0 },
    ];
    const ctxs = buildContextsFromCheck([], profiles);
    const byName = Object.fromEntries(ctxs.map((c) => [c.name, c]));

    expect(byName["email"]!.inferredType).toBe(ColumnType.EMAIL);
    expect(byName["first_name"]!.inferredType).toBe(ColumnType.NAME);
    // Bands assigned (>= 3 string columns).
    expect(byName["status"]!.cardinalityBand).toBe(CardinalityBand.LOW);
    // Low-cardinality status (no name signal, low band) is not an identifier.
    expect(byName["status"]!.isIdentifier).toBe(false);
  });

  it("downgrades a name-pattern column with low cardinality", () => {
    const profiles: ColumnProfileLike[] = [
      { name: "first_name", inferredType: "String", uniqueCount: 2, nullPct: 0 },
      { name: "city", inferredType: "String", uniqueCount: 80, nullPct: 0 },
      { name: "email", inferredType: "String", uniqueCount: 100, nullPct: 0 },
      { name: "notes", inferredType: "String", uniqueCount: 90, nullPct: 0 },
    ];
    const ctxs = buildContextsFromCheck([], profiles);
    const first = ctxs.find((c) => c.name === "first_name")!;
    // Name signal but lowest cardinality -> downgraded to non-identifier.
    expect(first.inferredType).toBe(ColumnType.NAME);
    expect(first.isIdentifier).toBe(false);
  });

  it("attaches findings to the matching column", () => {
    const profiles: ColumnProfileLike[] = [
      { name: "email", inferredType: "String", uniqueCount: 10, nullPct: 0 },
    ];
    const ctxs = buildContextsFromCheck(
      [{ column: "email", check: "format", message: "bad email" }],
      profiles,
    );
    expect(ctxs[0]!.findings).toEqual(["format: bad email"]);
  });
});

describe("enrichContextsFromFlow", () => {
  it("records applied transforms and confirms date type", () => {
    const ctxs = [
      makeColumnContext({ name: "signup", inferredType: ColumnType.STRING, isIdentifier: true }),
      makeColumnContext({ name: "name", inferredType: ColumnType.NAME }),
    ];
    enrichContextsFromFlow(ctxs, [
      { column: "signup", transform: "parse_date", affectedRows: 3 },
      { column: "name", transform: "strip", affectedRows: 5 },
      { column: "name", transform: "title_case", affectedRows: 0 },
    ]);
    const signup = ctxs.find((c) => c.name === "signup")!;
    expect(signup.inferredType).toBe(ColumnType.DATE);
    expect(signup.isIdentifier).toBe(false);
    const name = ctxs.find((c) => c.name === "name")!;
    // affectedRows>0 transform recorded; affectedRows=0 transform skipped.
    expect(name.transformsApplied).toEqual(["strip"]);
  });

  it("is a no-op with null records", () => {
    const ctxs = [makeColumnContext({ name: "a" })];
    expect(() => enrichContextsFromFlow(ctxs, null)).not.toThrow();
  });
});

describe("row helpers", () => {
  const rows = [
    { a: "x", b: "" },
    { a: "y", b: null },
    { a: "x", b: "z" },
  ];
  it("distinctNonNull ignores nullish/empty", () => {
    expect(distinctNonNull(rows, "a")).toBe(2);
    expect(distinctNonNull(rows, "b")).toBe(1);
  });
  it("nullRateOf counts nullish/empty as null", () => {
    expect(nullRateOf(rows, "b")).toBeCloseTo(2 / 3, 5);
    expect(nullRateOf([], "a")).toBe(1.0);
  });
});
