import { describe, it, expect } from "vitest";
import {
  HIGH_TRUST_SOURCES,
  trustForSource,
  correctionToJSON,
  correctionFromJSON,
  type Correction,
} from "../../src/core/memory/types.js";

describe("Correction source/decision types", () => {
  it("HIGH_TRUST_SOURCES contains exactly steward/boost/unmerge", () => {
    expect(HIGH_TRUST_SOURCES.size).toBe(3);
    expect(HIGH_TRUST_SOURCES.has("steward")).toBe(true);
    expect(HIGH_TRUST_SOURCES.has("boost")).toBe(true);
    expect(HIGH_TRUST_SOURCES.has("unmerge")).toBe(true);
  });

  it("trustForSource maps high-trust to 1.0 and others to 0.5", () => {
    expect(trustForSource("steward")).toBe(1.0);
    expect(trustForSource("boost")).toBe(1.0);
    expect(trustForSource("unmerge")).toBe(1.0);
    expect(trustForSource("agent")).toBe(0.5);
    expect(trustForSource("llm")).toBe(0.5);
    expect(trustForSource("api")).toBe(0.5);
  });

  it("Correction JSON round-trip is identity", () => {
    const c: Correction = {
      id: "abc-123",
      idA: 5,
      idB: 7,
      decision: "reject",
      source: "steward",
      trust: 1.0,
      fieldHash: "abc123",
      recordHash: "abc123:def456",
      originalScore: 0.92,
      matchkeyName: "identity",
      reason: null,
      dataset: "customers",
      createdAt: new Date("2026-05-04T12:00:00.000Z"),
    };
    const r = correctionFromJSON(correctionToJSON(c));
    expect(r).toEqual(c);
  });

  it("correctionToJSON emits snake_case keys and ISO-8601 UTC", () => {
    const c: Correction = {
      id: "u1",
      idA: 1,
      idB: 2,
      decision: "approve",
      source: "agent",
      trust: 0.5,
      fieldHash: "ff",
      recordHash: "aa:bb",
      originalScore: 0.5,
      matchkeyName: null,
      reason: null,
      dataset: null,
      createdAt: new Date("2026-05-04T12:00:00.000Z"),
    };
    const j = correctionToJSON(c);
    expect(j.id_a).toBe(1);
    expect(j.id_b).toBe(2);
    expect(j.field_hash).toBe("ff");
    expect(j.record_hash).toBe("aa:bb");
    expect(j.original_score).toBe(0.5);
    expect(j.matchkey_name).toBeNull();
    expect(j.created_at).toBe("2026-05-04T12:00:00Z");
  });
});

// TODO(phase 1.3): The pre-v0.4.0 tests for InMemoryStore + MemoryLearner
// (verdict/feature shape, sync API) are deleted here. They will be rewritten
// against the new async MemoryStore interface in phase 1.3 of the TS parity
// learning-memory plan.
