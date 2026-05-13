import { describe, expect, it } from "vitest";

import {
  findByRecord,
  getEntity,
  InMemoryIdentityStore,
  manualMerge,
  manualSplit,
  newEntityId,
  type EvidenceEdge,
  type IdentityNode,
  type SourceRecord,
} from "../../src/core/identity/index.js";

function makeNode(entityId: string, dataset = "d", confidence = 0.9): IdentityNode {
  const now = new Date();
  return {
    entityId,
    status: "active",
    mergedInto: null,
    goldenRecord: null,
    confidence,
    dataset,
    createdAt: now,
    updatedAt: now,
  };
}

function makeRecord(
  recordId: string,
  entityId: string | null,
  source = "src",
  dataset = "d",
): SourceRecord {
  const now = new Date();
  return {
    recordId,
    source,
    sourcePk: recordId.split(":").slice(1).join(":"),
    recordHash: `h-${recordId}`,
    entityId,
    payload: null,
    dataset,
    firstSeenAt: now,
    lastSeenAt: now,
  };
}

describe("newEntityId", () => {
  it("produces 36-char dashed UUIDs", () => {
    const ids = new Set<string>();
    for (let i = 0; i < 100; i++) ids.add(newEntityId());
    expect(ids.size).toBe(100);
    for (const id of ids) {
      expect(id.length).toBe(36);
      expect(id.split("-").length).toBe(5);
    }
  });
});

describe("InMemoryIdentityStore", () => {
  it("upserts and gets an identity", async () => {
    const s = new InMemoryIdentityStore();
    const eid = newEntityId();
    await s.upsertIdentity(makeNode(eid));
    const fetched = await s.getIdentity(eid);
    expect(fetched?.entityId).toBe(eid);
    expect(fetched?.status).toBe("active");
  });

  it("upserts source records and looks them up", async () => {
    const s = new InMemoryIdentityStore();
    const eid = newEntityId();
    await s.upsertIdentity(makeNode(eid));
    await s.upsertRecord(makeRecord("src:1", eid));
    await s.upsertRecord(makeRecord("src:2", eid));
    expect(await s.findEntityByRecord("src:1")).toBe(eid);
    const lookup = await s.lookupEntityIds(["src:1", "src:2", "src:missing"]);
    expect(lookup.get("src:1")).toBe(eid);
    expect(lookup.has("src:missing")).toBe(false);
    expect((await s.getRecordsForEntity(eid)).length).toBe(2);
  });

  it("canonicalizes edge record pair and dedups by run", async () => {
    const s = new InMemoryIdentityStore();
    const eid = newEntityId();
    await s.upsertIdentity(makeNode(eid));
    const edge: EvidenceEdge = {
      edgeId: null,
      entityId: eid,
      recordAId: "z:9",
      recordBId: "a:1",
      kind: "same_as",
      score: 0.95,
      matchkeyName: null,
      fieldScores: null,
      negativeEvidence: null,
      controllerSnapshot: null,
      runName: "r1",
      dataset: "d",
      recordedAt: new Date(),
    };
    await s.addEdge(edge);
    await s.addEdge(edge); // dup -> noop
    const edges = await s.edgesForEntity(eid);
    expect(edges.length).toBe(1);
    expect(edges[0]!.recordAId).toBe("a:1");
    expect(edges[0]!.recordBId).toBe("z:9");
  });

  it("emits and retrieves history", async () => {
    const s = new InMemoryIdentityStore();
    const eid = newEntityId();
    await s.upsertIdentity(makeNode(eid));
    await s.emitEvent({
      eventId: null, entityId: eid, kind: "created",
      payload: null, runName: "r1", dataset: "d", recordedAt: new Date(),
    });
    const events = await s.history(eid);
    expect(events.length).toBe(1);
    expect(events[0]!.kind).toBe("created");
    expect(await s.hasRunEvent(eid, "r1", "created")).toBe(true);
    expect(await s.hasRunEvent(eid, "r999", "created")).toBe(false);
  });

  it("retires with merged_into", async () => {
    const s = new InMemoryIdentityStore();
    const a = newEntityId();
    const b = newEntityId();
    await s.upsertIdentity(makeNode(a));
    await s.upsertIdentity(makeNode(b));
    await s.retireIdentity(a, b);
    const aNode = await s.getIdentity(a);
    expect(aNode?.status).toBe("merged_into");
    expect(aNode?.mergedInto).toBe(b);
  });

  it("listIdentities filters + paginates", async () => {
    const s = new InMemoryIdentityStore();
    for (let i = 0; i < 5; i++) await s.upsertIdentity(makeNode(newEntityId(), "d1"));
    for (let i = 0; i < 2; i++) await s.upsertIdentity(makeNode(newEntityId(), "d2"));
    expect((await s.listIdentities({ dataset: "d1" })).length).toBe(5);
    expect((await s.listIdentities({ dataset: "d1", limit: 3 })).length).toBe(3);
  });
});

describe("query helpers", () => {
  it("getEntity bundles records + edges + events", async () => {
    const s = new InMemoryIdentityStore();
    const eid = newEntityId();
    await s.upsertIdentity(makeNode(eid));
    await s.upsertRecord(makeRecord("src:1", eid));
    await s.upsertRecord(makeRecord("src:2", eid));
    await s.addEdge({
      edgeId: null, entityId: eid, recordAId: "src:1", recordBId: "src:2",
      kind: "same_as", score: 0.95, matchkeyName: null,
      fieldScores: null, negativeEvidence: null, controllerSnapshot: null,
      runName: "r", dataset: "d", recordedAt: new Date(),
    });
    const view = await getEntity(s, eid);
    expect(view?.records.length).toBe(2);
    expect(view?.edges.length).toBe(1);
  });

  it("findByRecord resolves through the record table", async () => {
    const s = new InMemoryIdentityStore();
    const eid = newEntityId();
    await s.upsertIdentity(makeNode(eid));
    await s.upsertRecord(makeRecord("src:7", eid));
    const view = await findByRecord(s, "src:7");
    expect(view?.node.entityId).toBe(eid);
    expect(await findByRecord(s, "missing")).toBeNull();
  });

  it("manualMerge reassigns records and retires loser", async () => {
    const s = new InMemoryIdentityStore();
    const a = newEntityId();
    const b = newEntityId();
    await s.upsertIdentity(makeNode(a));
    await s.upsertIdentity(makeNode(b));
    await s.upsertRecord(makeRecord("a:1", a));
    await s.upsertRecord(makeRecord("a:2", a));
    await s.upsertRecord(makeRecord("b:1", b));

    const out = await manualMerge(s, a, b, { reason: "dup" });
    expect(out.keep).toBe(a);

    expect((await s.getRecordsForEntity(b)).length).toBe(0);
    expect((await s.getRecordsForEntity(a)).length).toBe(3);
    expect((await s.getIdentity(b))?.status).toBe("merged_into");
  });

  it("manualSplit detaches subset into new identity", async () => {
    const s = new InMemoryIdentityStore();
    const a = newEntityId();
    await s.upsertIdentity(makeNode(a));
    await s.upsertRecord(makeRecord("a:1", a));
    await s.upsertRecord(makeRecord("a:2", a));
    await s.upsertRecord(makeRecord("a:3", a));

    const out = await manualSplit(s, a, ["a:2", "a:3"], { reason: "wrong merge" });
    expect(out.moved.length).toBe(2);
    expect((await s.getRecordsForEntity(a)).length).toBe(1);
    expect((await s.getRecordsForEntity(out.newEntityId)).length).toBe(2);
  });

  it("manualMerge rejects unknown entities", async () => {
    const s = new InMemoryIdentityStore();
    const a = newEntityId();
    await s.upsertIdentity(makeNode(a));
    await expect(manualMerge(s, a, "missing")).rejects.toThrow();
  });

  it("manualSplit rejects empty record list", async () => {
    const s = new InMemoryIdentityStore();
    const a = newEntityId();
    await s.upsertIdentity(makeNode(a));
    await expect(manualSplit(s, a, [])).rejects.toThrow();
  });
});
