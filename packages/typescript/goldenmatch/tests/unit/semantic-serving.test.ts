/**
 * Edge-safe key-integrity certifier + Customer 360 serving-join certificate.
 * Port parity with Python tests/test_semantic_serving.py (the certify_serving_joins
 * cases) + the structural certify_key_integrity behavior.
 */
import { describe, it, expect } from "vitest";

import { certifyKeyIntegrity } from "../../src/core/semantic/keyIntegrity.js";
import { certifyServingJoins, ServingJoinCertificate } from "../../src/core/semantic/serving.js";
import { InMemoryIdentityStore } from "../../src/core/identity/in-memory-store.js";

const NOW = new Date("2026-01-01T00:00:00.000Z");

describe("certifyKeyIntegrity (structural tier)", () => {
  it("a unique key is trustworthy: estimate 1.0, no duplicate groups", () => {
    const cert = certifyKeyIntegrity({ id: ["a", "b", "c"] }, { key: "id" });
    expect(cert.isUniqueAtGrain).toBe(true);
    expect(cert.nRows).toBe(3);
    expect(cert.nKeyGroups).toBe(3);
    expect(cert.duplicateKeyGroups).toBe(0);
    expect(cert.maxFanOut).toBe(1);
    expect(cert.estimate).toBe(1.0);
    expect(cert.isTrustworthy()).toBe(true);
  });

  it("a duplicated key reports fan-out and is not trustworthy", () => {
    const cert = certifyKeyIntegrity({ id: ["a", "a", "b"] }, { key: "id" });
    expect(cert.isUniqueAtGrain).toBe(false);
    expect(cert.nKeyGroups).toBe(2);
    expect(cert.duplicateKeyGroups).toBe(1);
    expect(cert.maxFanOut).toBe(2);
    expect(cert.estimate).toBe(0.5);
    expect(cert.isTrustworthy()).toBe(false);
  });

  it("null is a distinct key group (not conflated with the string 'null')", () => {
    const cert = certifyKeyIntegrity({ id: [null, "null"] }, { key: "id" });
    expect(cert.nKeyGroups).toBe(2);
    expect(cert.isUniqueAtGrain).toBe(true);
  });

  it("quantifies per-measure SUM fan-out for a duplicated key", () => {
    // key a repeats with amounts 10 and 30 (max 30); dedup SUM = 30 + 5 = 35,
    // raw SUM = 10 + 30 + 5 = 45 -> fan-out 45/35.
    const cert = certifyKeyIntegrity(
      { id: ["a", "a", "b"], amount: [10, 30, 5] },
      { key: "id", measures: ["amount"] },
    );
    expect(cert.measureFanOut["amount"]).toBeCloseTo(45 / 35, 10);
  });

  it("rejects a missing key column", () => {
    expect(() => certifyKeyIntegrity({ id: ["a"] }, { key: "nope" })).toThrow(/key column/);
  });
});

async function seedStore(): Promise<InMemoryIdentityStore> {
  const store = new InMemoryIdentityStore();
  for (const [entityId, recordId] of [
    ["E1", "crm:1"],
    ["E1", "crm:2"],
    ["E2", "crm:3"],
  ] as const) {
    if (!(await store.getIdentity(entityId))) {
      await store.upsertIdentity({
        entityId,
        status: "active",
        mergedInto: null,
        goldenRecord: null,
        confidence: 0.9,
        dataset: "crm",
        createdAt: NOW,
        updatedAt: NOW,
      });
    }
    await store.upsertRecord({
      recordId,
      source: "crm",
      sourcePk: recordId.split(":")[1]!,
      recordHash: recordId,
      entityId,
      payload: null,
      dataset: "crm",
      firstSeenAt: NOW,
      lastSeenAt: NOW,
    });
  }
  return store;
}

describe("certifyServingJoins", () => {
  it("clean store: 3 distinct records across 2 entities -> trustworthy", async () => {
    const store = await seedStore();
    const cert = await certifyServingJoins(store, { dataset: "crm" });
    expect(cert).toBeInstanceOf(ServingJoinCertificate);
    expect(cert.nEntities).toBe(2);
    expect(cert.nRecords).toBe(3);
    expect(cert.isTrustworthy).toBe(true);
    expect(cert.truncated).toBe(false);
    expect(cert.recordCertificate.duplicateKeyGroups).toBe(0);
  });

  it("an empty store is trivially trustworthy", async () => {
    const store = new InMemoryIdentityStore();
    const cert = await certifyServingJoins(store);
    expect(cert.nEntities).toBe(0);
    expect(cert.nRecords).toBe(0);
    expect(cert.isTrustworthy).toBe(true);
  });

  it("respects maxEntities and reports truncated", async () => {
    const store = new InMemoryIdentityStore();
    for (let i = 0; i < 5; i++) {
      const entityId = `E${i}`;
      await store.upsertIdentity({
        entityId,
        status: "active",
        mergedInto: null,
        goldenRecord: null,
        confidence: 0.9,
        dataset: "crm",
        createdAt: NOW,
        updatedAt: NOW,
      });
      await store.upsertRecord({
        recordId: `crm:${i}`,
        source: "crm",
        sourcePk: String(i),
        recordHash: `crm:${i}`,
        entityId,
        payload: null,
        dataset: "crm",
        firstSeenAt: NOW,
        lastSeenAt: NOW,
      });
    }
    const cert = await certifyServingJoins(store, { dataset: "crm", pageSize: 2, maxEntities: 3 });
    expect(cert.nEntities).toBe(3);
    expect(cert.truncated).toBe(true);
  });
});
