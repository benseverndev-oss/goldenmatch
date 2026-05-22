/**
 * Field-level Correction round-trip (Phase 5 of v1.18 surface-sync
 * roadmap, TS port v2.0 foundation).
 *
 * Verifies that the v1.18.2 #437 field-level fields (`fieldName`,
 * `originalValue`, `correctedValue`) + `decision: "field_correct"` +
 * the new `"rest"` / `"duckdb"` CorrectionSource values round-trip
 * through the in-memory store.
 *
 * SQLite-backed sqlite-store.ts has the same schema additions but
 * needs `better-sqlite3` installed for its tests -- covered in the
 * existing parity-test suite.
 */
import { describe, expect, it } from "vitest";

import { InMemoryStore } from "../src/core/memory/store.js";
import {
  type Correction,
  trustForSource,
} from "../src/core/memory/types.js";

function makeFieldCorrection(overrides: Partial<Correction> = {}): Correction {
  return {
    id: "fc-1",
    idA: 42,
    idB: 0,
    decision: "field_correct",
    source: "steward",
    trust: 1.0,
    fieldHash: "",
    recordHash: "",
    originalScore: 0.0,
    matchkeyName: null,
    reason: null,
    dataset: "customers",
    createdAt: new Date("2026-05-22T18:00:00Z"),
    fieldName: "address1",
    originalValue: "1 Elm St",
    correctedValue: "1 Elm Street, Apt 4B",
    ...overrides,
  };
}

describe("field-level Correction (Phase 5 foundation)", () => {
  it("round-trips via InMemoryStore", async () => {
    const store = new InMemoryStore({
      enabled: true,
      backend: "memory",
      learning: { thresholdMinCorrections: 10, weightsMinCorrections: 50 },
    });
    const correction = makeFieldCorrection();
    await store.addCorrection(correction);
    const out = await store.getCorrections({ dataset: "customers" });
    expect(out).toHaveLength(1);
    expect(out[0]!.decision).toBe("field_correct");
    expect(out[0]!.fieldName).toBe("address1");
    expect(out[0]!.correctedValue).toBe("1 Elm Street, Apt 4B");
  });

  it("pair-level Correction still works without field-level fields", async () => {
    const store = new InMemoryStore({
      enabled: true,
      backend: "memory",
      learning: { thresholdMinCorrections: 10, weightsMinCorrections: 50 },
    });
    await store.addCorrection({
      id: "pl-1",
      idA: 5,
      idB: 10,
      decision: "approve",
      source: "steward",
      trust: 1.0,
      fieldHash: "",
      recordHash: "",
      originalScore: 0.95,
      matchkeyName: null,
      reason: null,
      dataset: "customers",
      createdAt: new Date("2026-05-22T18:00:00Z"),
    });
    const out = await store.getCorrections({ dataset: "customers" });
    expect(out).toHaveLength(1);
    expect(out[0]!.decision).toBe("approve");
    expect(out[0]!.fieldName ?? null).toBeNull();
    expect(out[0]!.correctedValue ?? null).toBeNull();
  });
});

describe("trustForSource (Phase 5: new sources)", () => {
  it("steward / boost / unmerge -> 1.0", () => {
    expect(trustForSource("steward")).toBe(1.0);
    expect(trustForSource("boost")).toBe(1.0);
    expect(trustForSource("unmerge")).toBe(1.0);
  });

  it("rest -> 0.8 (Phase 2 source)", () => {
    expect(trustForSource("rest")).toBe(0.8);
  });

  it("duckdb -> 0.7 (Phase 6B source)", () => {
    expect(trustForSource("duckdb")).toBe(0.7);
  });

  it("agent / llm / api -> 0.5", () => {
    expect(trustForSource("agent")).toBe(0.5);
    expect(trustForSource("llm")).toBe(0.5);
    expect(trustForSource("api")).toBe(0.5);
  });
});
