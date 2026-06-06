/**
 * identity-query-helpers.test.ts -- Wave 4 TS parity: the module-level
 * `findConflicts` / `history` query helpers (mirror Python query.py). They
 * wrap the store methods so library consumers don't have to hold a store
 * reference's method surface directly.
 */
import { describe, it, expect, beforeAll } from "vitest";
import {
  InMemoryIdentityStore,
  findConflicts,
  history,
  type IdentityNode,
  type EvidenceEdge,
} from "../../src/core/identity/index.js";

const store = new InMemoryIdentityStore();

function makeNode(entityId: string, dataset = "test"): IdentityNode {
  const now = new Date();
  return {
    entityId,
    status: "active",
    mergedInto: null,
    goldenRecord: { name: entityId },
    confidence: 0.9,
    dataset,
    createdAt: now,
    updatedAt: now,
  };
}

beforeAll(async () => {
  await store.upsertIdentity(makeNode("e-1"));
  const edge: EvidenceEdge = {
    edgeId: null,
    entityId: "e-1",
    recordAId: "csv:r1",
    recordBId: "csv:r2",
    kind: "conflicts_with",
    score: 0.5,
    matchkeyName: "identity",
    fieldScores: null,
    negativeEvidence: null,
    controllerSnapshot: null,
    runName: "test-run",
    dataset: "test",
    recordedAt: new Date(),
  };
  await store.addEdge(edge);
  await store.emitEvent({
    eventId: null,
    entityId: "e-1",
    kind: "created",
    payload: { note: "seed" },
    runName: "test-run",
    dataset: "test",
    recordedAt: new Date(),
  });
});

describe("identity query helpers (Wave 4 parity)", () => {
  it("findConflicts returns conflict edges", async () => {
    const conflicts = await findConflicts(store, "test");
    expect(conflicts.length).toBe(1);
    expect(conflicts[0]!.kind).toBe("conflicts_with");
    expect(conflicts[0]!.entityId).toBe("e-1");
  });

  it("history returns the entity's event log", async () => {
    const events = await history(store, "e-1");
    expect(events.length).toBeGreaterThanOrEqual(1);
    expect(events.some((e) => e.kind === "created")).toBe(true);
  });
});
