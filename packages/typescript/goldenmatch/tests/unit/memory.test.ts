import { describe, it, expect } from "vitest";
import {
  HIGH_TRUST_SOURCES,
  trustForSource,
  correctionToJSON,
  correctionFromJSON,
  type Correction,
} from "../../src/core/memory/types.js";
import { InMemoryStore } from "../../src/core/memory/store.js";

function makeCorrection(overrides: Partial<Correction> = {}): Correction {
  return {
    id: "c1",
    idA: 1,
    idB: 2,
    decision: "approve",
    source: "agent",
    trust: 0.5,
    fieldHash: "field-hash",
    recordHash: "rec-a:rec-b",
    originalScore: 0.8,
    matchkeyName: "identity",
    reason: null,
    dataset: null,
    createdAt: new Date("2026-05-04T12:00:00.000Z"),
    ...overrides,
  };
}

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

describe("InMemoryStore", () => {
  it("addCorrection then getCorrection round-trips with canonicalization", async () => {
    const store = new InMemoryStore();
    const c = makeCorrection({ idA: 5, idB: 3 });
    await store.addCorrection(c);

    // Lookup either ordering returns the same correction with canonical (3, 5).
    const r1 = await store.getCorrection(5, 3, null);
    const r2 = await store.getCorrection(3, 5, null);
    expect(r1).not.toBeNull();
    expect(r1!.idA).toBe(3);
    expect(r1!.idB).toBe(5);
    expect(r2).toEqual(r1);
  });

  it("getCorrections returns all in canonical order", async () => {
    const store = new InMemoryStore();
    await store.addCorrection(makeCorrection({ id: "c1", idA: 5, idB: 3 }));
    await store.addCorrection(makeCorrection({ id: "c2", idA: 7, idB: 9 }));
    const all = await store.getCorrections();
    expect(all).toHaveLength(2);
    for (const c of all) {
      expect(c.idA).toBeLessThanOrEqual(c.idB);
    }
    const ids = all.map((c) => c.id).sort();
    expect(ids).toEqual(["c1", "c2"]);
  });

  it("trust upsert: lower trust ignored", async () => {
    const store = new InMemoryStore();
    await store.addCorrection(
      makeCorrection({ id: "high", trust: 1.0, source: "steward", decision: "approve" }),
    );
    await store.addCorrection(
      makeCorrection({ id: "low", trust: 0.5, source: "agent", decision: "reject" }),
    );
    const r = await store.getCorrection(1, 2, null);
    expect(r!.id).toBe("high");
    expect(r!.decision).toBe("approve");
    expect(r!.trust).toBe(1.0);
  });

  it("trust upsert: same-tier latest wins", async () => {
    const store = new InMemoryStore();
    await store.addCorrection(
      makeCorrection({ id: "first", trust: 0.5, decision: "approve" }),
    );
    await store.addCorrection(
      makeCorrection({ id: "second", trust: 0.5, decision: "reject" }),
    );
    const r = await store.getCorrection(1, 2, null);
    expect(r!.id).toBe("second");
    expect(r!.decision).toBe("reject");
  });

  it("dataset scoping: corrections in different datasets don't collide", async () => {
    const store = new InMemoryStore();
    await store.addCorrection(
      makeCorrection({ id: "ds-a", dataset: "customers", decision: "approve" }),
    );
    await store.addCorrection(
      makeCorrection({ id: "ds-b", dataset: "vendors", decision: "reject" }),
    );

    const customers = await store.getCorrection(1, 2, "customers");
    const vendors = await store.getCorrection(1, 2, "vendors");
    expect(customers!.id).toBe("ds-a");
    expect(vendors!.id).toBe("ds-b");

    const onlyCustomers = await store.getCorrections({ dataset: "customers" });
    expect(onlyCustomers).toHaveLength(1);
    expect(onlyCustomers[0]!.id).toBe("ds-a");

    expect(await store.countCorrections("customers")).toBe(1);
    expect(await store.countCorrections()).toBe(2);
  });
});
