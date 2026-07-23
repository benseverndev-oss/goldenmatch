/**
 * Edge-safe core tests for claimRecord + mediateConflict/openConflicts and the
 * new edgesByKind store method, exercised against InMemoryIdentityStore.
 * Mirrors the durable-backend coverage in
 * tests/node/identity/sqlite-store.test.ts. Ports the assertions of the Python
 * tests/identity/test_mediation.py surface.
 */

import { describe, it, expect, beforeEach } from "vitest";

import { InMemoryIdentityStore } from "../../src/core/identity/in-memory-store.js";
import { claimRecord } from "../../src/core/identity/query.js";
import { mediateConflict, openConflicts } from "../../src/core/identity/mediation.js";
import type { IdentityNode, SourceRecord } from "../../src/core/identity/types.js";

const NOW = new Date("2026-01-01T00:00:00.000Z");

function node(entityId: string): IdentityNode {
  return {
    entityId,
    status: "active",
    mergedInto: null,
    goldenRecord: null,
    confidence: 0.9,
    dataset: "d",
    createdAt: NOW,
    updatedAt: NOW,
  };
}

function record(recordId: string, entityId: string): SourceRecord {
  return {
    recordId,
    source: "src",
    sourcePk: recordId.split(":")[1] ?? recordId,
    recordHash: `h-${recordId}`,
    entityId,
    payload: null,
    dataset: "d",
    firstSeenAt: NOW,
    lastSeenAt: NOW,
  };
}

let store: InMemoryIdentityStore;

beforeEach(async () => {
  store = new InMemoryIdentityStore();
});

describe("claimRecord", () => {
  it("reassigns a record and emits claimed on both gaining + losing entities", async () => {
    await store.upsertIdentity(node("E1"));
    await store.upsertIdentity(node("E2"));
    await store.upsertRecord(record("src:1", "E2"));

    const res = await claimRecord(store, "E1", "src:1", { reason: "manual claim" });
    expect(res.moved).toBe(true);
    expect(res.fromEntity).toBe("E2");
    expect((await store.getRecord("src:1"))?.entityId).toBe("E1");

    const e1Events = await store.history("E1");
    const e2Events = await store.history("E2");
    expect(e1Events.some((e) => e.kind === "claimed")).toBe(true);
    expect(e2Events.some((e) => e.kind === "claimed")).toBe(true);
  });

  it("is a no-op when the record already belongs to the target", async () => {
    await store.upsertIdentity(node("E1"));
    await store.upsertRecord(record("src:1", "E1"));
    const res = await claimRecord(store, "E1", "src:1");
    expect(res.moved).toBe(false);
    // No claimed event emitted on a no-op replay.
    expect((await store.history("E1")).some((e) => e.kind === "claimed")).toBe(false);
  });

  it("throws on a missing entity or record", async () => {
    await store.upsertIdentity(node("E1"));
    await expect(claimRecord(store, "E1", "nope:0")).rejects.toThrow(/Record/);
    await store.upsertRecord(record("src:1", "E1"));
    await expect(claimRecord(store, "ghost", "src:1")).rejects.toThrow(/not found/);
  });
});

describe("edgesByKind", () => {
  it("returns only edges of the requested kind, newest-first", async () => {
    await store.addEdge({
      edgeId: null,
      entityId: "E1",
      recordAId: "src:1",
      recordBId: "src:2",
      kind: "conflicts_with",
      score: 0.4,
      matchkeyName: null,
      fieldScores: null,
      negativeEvidence: null,
      controllerSnapshot: null,
      runName: "r",
      dataset: "d",
      recordedAt: NOW,
    });
    await store.addEdge({
      edgeId: null,
      entityId: "E1",
      recordAId: "src:1",
      recordBId: "src:2",
      kind: "mediation_verdict",
      score: null,
      matchkeyName: null,
      fieldScores: null,
      negativeEvidence: { resolution: "defer" },
      controllerSnapshot: null,
      runName: "mediation:a",
      dataset: "d",
      recordedAt: NOW,
    });
    const verdicts = await store.edgesByKind("mediation_verdict");
    expect(verdicts).toHaveLength(1);
    expect(verdicts[0]!.kind).toBe("mediation_verdict");
    expect(await store.edgesByKind("mediation_verdict", "other")).toHaveLength(0);
  });
});

describe("mediateConflict / openConflicts", () => {
  async function seedConflict(): Promise<void> {
    await store.upsertIdentity(node("E1"));
    await store.upsertRecord(record("src:1", "E1"));
    await store.upsertRecord(record("src:2", "E1"));
    await store.addEdge({
      edgeId: null,
      entityId: "E1",
      recordAId: "src:1",
      recordBId: "src:2",
      kind: "conflicts_with",
      score: 0.4,
      matchkeyName: "name",
      fieldScores: null,
      negativeEvidence: { reason: "weak bottleneck" },
      controllerSnapshot: null,
      runName: "r",
      dataset: "d",
      recordedAt: NOW,
    });
  }

  it("'same' keeps the entity and closes the conflict", async () => {
    await seedConflict();
    expect(await openConflicts(store)).toHaveLength(1);
    const res = await mediateConflict(store, "src:1", "src:2", "same");
    expect(res.action.type).toBe("none");
    expect((await store.getRecord("src:2"))?.entityId).toBe("E1");
    expect(await openConflicts(store)).toHaveLength(0);
    // conflict_mediated event recorded.
    expect((await store.history("E1")).some((e) => e.kind === "conflict_mediated")).toBe(true);
  });

  it("'distinct' splits record_b out via manualSplit and closes the conflict", async () => {
    await seedConflict();
    const res = await mediateConflict(store, "src:1", "src:2", "distinct");
    expect(res.action.type).toBe("split");
    expect((await store.getRecord("src:2"))?.entityId).not.toBe("E1");
    expect((await store.getRecord("src:1"))?.entityId).toBe("E1");
    expect(await openConflicts(store)).toHaveLength(0);
  });

  it("'defer' keeps the conflict open but logs the verdict", async () => {
    await seedConflict();
    const res = await mediateConflict(store, "src:1", "src:2", "defer");
    expect(res.action.type).toBe("none");
    const open = await openConflicts(store);
    expect(open).toHaveLength(1);
    expect(open[0]!.deferred).toBe(true);
    // includeDeferred:false hides it.
    expect(await openConflicts(store, { includeDeferred: false })).toHaveLength(0);
    // a verdict edge exists.
    expect(await store.edgesByKind("mediation_verdict")).toHaveLength(1);
  });

  it("re-mediating the same pair appends a new verdict (not a silent no-op)", async () => {
    await seedConflict();
    await mediateConflict(store, "src:1", "src:2", "defer");
    expect(await store.edgesByKind("mediation_verdict")).toHaveLength(1);
    expect(await openConflicts(store)).toHaveLength(1);
    // Re-adjudicate the SAME pair -> a SECOND verdict edge, latest wins, conflict closes.
    await mediateConflict(store, "src:1", "src:2", "distinct");
    expect(await store.edgesByKind("mediation_verdict")).toHaveLength(2);
    expect(await openConflicts(store)).toHaveLength(0);
  });

  it("rejects an invalid resolution", async () => {
    await seedConflict();
    await expect(mediateConflict(store, "src:1", "src:2", "bogus")).rejects.toThrow(/Invalid resolution/);
  });
});
