/**
 * Unit tests for the host-measurement half of the #1207 strong-id blocking union
 * (`blockingUnionMeasure.ts`) — OR-coverage, per-pass scale-safety, and the
 * two-phase `tryStrongIdUnion` glue.
 */
import { describe, it, expect } from "vitest";
import {
  unionCoverage,
  idPassScaleSafe,
  passIsBounded,
  tryStrongIdUnion,
} from "../../src/core/blockingUnionMeasure.js";
import type { UnionColumn } from "../../src/core/blockingUnion.js";
import type { Row } from "../../src/core/types.js";

describe("unionCoverage", () => {
  const rows: Row[] = [
    { id: "A", email: null, first: "x", last: "y" },
    { id: null, email: "e@x", first: "x", last: "y" },
    { id: null, email: null, first: "x", last: "y" },
  ];

  it("OR across single-field passes", () => {
    // id covers row0, email covers row1 -> 2/3.
    expect(unionCoverage(rows, [["id"], ["email"]])).toBeCloseTo(2 / 3);
  });

  it("a multi-field pass needs ALL its fields present", () => {
    // [first,last] present on every row -> 1.0.
    expect(unionCoverage(rows, [["first", "last"]])).toBe(1);
  });

  it("union of id + [first,last] covers all rows", () => {
    expect(unionCoverage(rows, [["id"], ["first", "last"]])).toBe(1);
  });

  it("empty rows -> 0", () => {
    expect(unionCoverage([], [["id"]])).toBe(0);
  });
});

describe("scale-safety", () => {
  it("idPassScaleSafe measures the non-null subframe and honors the ceiling", () => {
    const rows: Row[] = [
      { id: "A" },
      { id: "A" },
      { id: "B" },
      { id: null },
    ];
    expect(idPassScaleSafe(rows, "id", 1000)).toBe(true);
    // max non-null block is 2 (two "A"s); a ceiling of 1 rejects it.
    expect(idPassScaleSafe(rows, "id", 1)).toBe(false);
  });

  it("idPassScaleSafe is false when there are no non-null rows", () => {
    expect(idPassScaleSafe([{ id: null }, { id: null }], "id", 1000)).toBe(false);
  });

  it("passIsBounded groups null-inclusive over the joined key", () => {
    const rows: Row[] = [
      { a: "x", b: "1" },
      { a: "x", b: "1" },
      { a: "y", b: "2" },
    ];
    expect(passIsBounded(rows, ["a", "b"], 1000)).toBe(true);
    expect(passIsBounded(rows, ["a", "b"], 1)).toBe(false); // ("x","1") block = 2
  });
});

describe("tryStrongIdUnion", () => {
  const cols: UnionColumn[] = [
    { name: "member_id", colType: "identifier", nullRate: 0.5, cardinalityRatio: 0.5 },
    { name: "email", colType: "email", nullRate: 0.5, cardinalityRatio: 0.5 },
    { name: "first_name", colType: "name", nullRate: 0.0, cardinalityRatio: 0.9 },
    { name: "last_name", colType: "name", nullRate: 0.0, cardinalityRatio: 0.9 },
  ];
  const rows: Row[] = [
    { member_id: "M1", email: null, first_name: "A", last_name: "P" },
    { member_id: null, email: "a@x", first_name: "A", last_name: "P" },
    { member_id: "M2", email: null, first_name: "B", last_name: "Q" },
    { member_id: null, email: "b@x", first_name: "B", last_name: "Q" },
  ];

  it("fires the union with strong-id + name passes and member_id primary", () => {
    const out = tryStrongIdUnion(cols, rows, 1000);
    expect(out).not.toBeNull();
    expect(out!.strategy).toBe("multi_pass");
    expect(out!.keys[0]!.fields).toEqual(["member_id"]);
    const passFields = out!.passes.map((p) => p.fields);
    expect(passFields).toContainEqual(["member_id"]);
    expect(passFields).toContainEqual(["email"]);
    expect(passFields).toContainEqual(["first_name", "last_name"]);
  });

  it("returns null when there is no strong id (bare first/last -> no name pass)", () => {
    const bareCols: UnionColumn[] = [
      { name: "first", colType: "name", nullRate: 0, cardinalityRatio: 0.9 },
      { name: "last", colType: "name", nullRate: 0, cardinalityRatio: 0.9 },
    ];
    const bareRows: Row[] = [{ first: "A", last: "P" }];
    expect(tryStrongIdUnion(bareCols, bareRows, 1000)).toBeNull();
  });

  it("returns null when coverage is below target", () => {
    // Only member_id present on 1/4 rows, no name passes assemble (no name cols).
    const idOnly: UnionColumn[] = [
      { name: "member_id", colType: "identifier", nullRate: 0.75, cardinalityRatio: 0.5 },
      { name: "email", colType: "email", nullRate: 0.75, cardinalityRatio: 0.5 },
    ];
    const sparse: Row[] = [
      { member_id: "M1", email: null },
      { member_id: null, email: null },
      { member_id: null, email: null },
      { member_id: null, email: "z@x" },
    ];
    // 2 strong-id passes assemble, but OR-coverage = 2/4 < 0.95 -> null.
    expect(tryStrongIdUnion(idOnly, sparse, 1000)).toBeNull();
  });
});
