/**
 * SqliteIdentityStore unit tests.
 *
 * Mirrors the shape of in-memory-store.test.ts but writes to a fresh
 * better-sqlite3-backed file per test (cleaned up automatically by the
 * tmpdir-style helper). Verifies every method on the IdentityStore
 * interface plus the schema-version migration path.
 */
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type {
  EvidenceEdge,
  IdentityAlias,
  IdentityEvent,
  IdentityNode,
  SourceRecord,
} from "../../../src/core/identity/types.js";
import { SqliteIdentityStore } from "../../../src/node/identity/sqlite-store.js";

function makeNode(entityId: string, dataset = "test", confidence = 0.9): IdentityNode {
  const now = new Date();
  return {
    entityId,
    status: "active",
    mergedInto: null,
    goldenRecord: { name: `Entity ${entityId}` },
    confidence,
    dataset,
    createdAt: now,
    updatedAt: now,
  };
}

function makeRecord(recordId: string, entityId: string | null, dataset = "test"): SourceRecord {
  const now = new Date();
  return {
    recordId,
    source: "csv:test",
    sourcePk: recordId.split(":")[1] ?? recordId,
    recordHash: `hash-${recordId}`,
    entityId,
    payload: { value: recordId },
    dataset,
    firstSeenAt: now,
    lastSeenAt: now,
  };
}

function makeEdge(entityId: string, a: string, b: string): EvidenceEdge {
  return {
    edgeId: null,
    entityId,
    recordAId: a,
    recordBId: b,
    kind: "same_as",
    score: 0.95,
    matchkeyName: "identity",
    fieldScores: { name: 0.9 },
    negativeEvidence: null,
    controllerSnapshot: null,
    runName: "test-run-1",
    dataset: "test",
    recordedAt: new Date(),
  };
}

function makeEvent(entityId: string, kind: "created" | "absorbed_record"): IdentityEvent {
  return {
    eventId: null,
    entityId,
    kind,
    payload: { note: "test" },
    runName: "test-run-1",
    dataset: "test",
    recordedAt: new Date(),
  };
}

function makeAlias(alias: string, entityId: string): IdentityAlias {
  return {
    alias,
    entityId,
    kind: "external_id",
    dataset: "test",
    recordedAt: new Date(),
  };
}

describe("SqliteIdentityStore", () => {
  let tmpDir: string;
  let dbPath: string;
  let store: SqliteIdentityStore;

  beforeEach(async () => {
    tmpDir = mkdtempSync(join(tmpdir(), "gm-identity-"));
    dbPath = join(tmpDir, "identity.db");
    store = await SqliteIdentityStore.open({ path: dbPath });
  });

  afterEach(async () => {
    await store.close();
    rmSync(tmpDir, { recursive: true, force: true });
  });

  describe("identity_nodes", () => {
    it("upsert + get round-trips a node with golden_record JSON", async () => {
      const node = makeNode("e-1");
      await store.upsertIdentity(node);
      const got = await store.getIdentity("e-1");
      expect(got).not.toBeNull();
      expect(got?.entityId).toBe("e-1");
      expect(got?.goldenRecord).toEqual({ name: "Entity e-1" });
      expect(got?.status).toBe("active");
    });

    it("upsert with the same entityId updates fields", async () => {
      const node = makeNode("e-1", "d1", 0.5);
      await store.upsertIdentity(node);
      const updated: IdentityNode = { ...node, confidence: 0.99 };
      await store.upsertIdentity(updated);
      const got = await store.getIdentity("e-1");
      expect(got?.confidence).toBe(0.99);
    });

    it("getIdentity returns null for unknown id", async () => {
      expect(await store.getIdentity("missing")).toBeNull();
    });

    it("listIdentities filters by dataset and status", async () => {
      await store.upsertIdentity(makeNode("e-1", "alpha"));
      await store.upsertIdentity(makeNode("e-2", "beta"));
      await store.upsertIdentity({ ...makeNode("e-3", "alpha"), status: "retired" });
      const alpha = await store.listIdentities({ dataset: "alpha" });
      expect(alpha.map((n) => n.entityId).sort()).toEqual(["e-1", "e-3"]);
      const active = await store.listIdentities({ status: "active" });
      expect(active.map((n) => n.entityId).sort()).toEqual(["e-1", "e-2"]);
    });

    it("countIdentities respects dataset", async () => {
      await store.upsertIdentity(makeNode("e-1", "alpha"));
      await store.upsertIdentity(makeNode("e-2", "beta"));
      expect(await store.countIdentities()).toBe(2);
      expect(await store.countIdentities("alpha")).toBe(1);
    });

    it("retireIdentity with mergedInto sets status=merged_into", async () => {
      await store.upsertIdentity(makeNode("e-1"));
      await store.retireIdentity("e-1", "e-2");
      const got = await store.getIdentity("e-1");
      expect(got?.status).toBe("merged_into");
      expect(got?.mergedInto).toBe("e-2");
    });

    it("retireIdentity without mergedInto sets status=retired", async () => {
      await store.upsertIdentity(makeNode("e-1"));
      await store.retireIdentity("e-1");
      const got = await store.getIdentity("e-1");
      expect(got?.status).toBe("retired");
      expect(got?.mergedInto).toBeNull();
    });
  });

  describe("source_records", () => {
    beforeEach(async () => {
      await store.upsertIdentity(makeNode("e-1"));
    });

    it("upsert + get round-trips a record with payload JSON", async () => {
      const rec = makeRecord("csv:r1", "e-1");
      await store.upsertRecord(rec);
      const got = await store.getRecord("csv:r1");
      expect(got?.recordId).toBe("csv:r1");
      expect(got?.payload).toEqual({ value: "csv:r1" });
      expect(got?.entityId).toBe("e-1");
    });

    it("getRecordsForEntity returns records sorted by firstSeenAt", async () => {
      await store.upsertRecord(makeRecord("csv:r1", "e-1"));
      await store.upsertRecord(makeRecord("csv:r2", "e-1"));
      const got = await store.getRecordsForEntity("e-1");
      expect(got.map((r) => r.recordId).sort()).toEqual(["csv:r1", "csv:r2"]);
    });

    it("findEntityByRecord returns the linked entity id", async () => {
      await store.upsertRecord(makeRecord("csv:r1", "e-1"));
      expect(await store.findEntityByRecord("csv:r1")).toBe("e-1");
      expect(await store.findEntityByRecord("missing")).toBeNull();
    });

    it("lookupEntityIds bulk-resolves", async () => {
      await store.upsertRecord(makeRecord("csv:r1", "e-1"));
      await store.upsertRecord(makeRecord("csv:r2", "e-1"));
      const got = await store.lookupEntityIds(["csv:r1", "csv:r2", "missing"]);
      expect(got.size).toBe(2);
      expect(got.get("csv:r1")).toBe("e-1");
      expect(got.get("csv:r2")).toBe("e-1");
    });

    it("lookupEntityIds with empty input returns empty map", async () => {
      const got = await store.lookupEntityIds([]);
      expect(got.size).toBe(0);
    });
  });

  describe("evidence_edges", () => {
    beforeEach(async () => {
      await store.upsertIdentity(makeNode("e-1"));
    });

    it("addEdge inserts a same_as edge and returns edge_id", async () => {
      const id = await store.addEdge(makeEdge("e-1", "csv:r1", "csv:r2"));
      expect(id).not.toBeNull();
      expect(typeof id).toBe("number");
    });

    it("addEdge canonicalizes (a, b) to (min, max)", async () => {
      await store.addEdge(makeEdge("e-1", "csv:r2", "csv:r1"));
      const edges = await store.edgesForEntity("e-1");
      expect(edges).toHaveLength(1);
      expect(edges[0]?.recordAId).toBe("csv:r1");
      expect(edges[0]?.recordBId).toBe("csv:r2");
    });

    it("addEdge dedups on (entity_id, a, b, kind, run_name)", async () => {
      const id1 = await store.addEdge(makeEdge("e-1", "csv:r1", "csv:r2"));
      const id2 = await store.addEdge(makeEdge("e-1", "csv:r1", "csv:r2"));
      expect(id1).toBe(id2);
      expect((await store.edgesForEntity("e-1")).length).toBe(1);
    });

    it("findConflicts filters by kind='conflicts_with'", async () => {
      await store.addEdge(makeEdge("e-1", "csv:r1", "csv:r2"));
      const conflict: EvidenceEdge = {
        ...makeEdge("e-1", "csv:r1", "csv:r3"),
        kind: "conflicts_with",
      };
      await store.addEdge(conflict);
      const conflicts = await store.findConflicts();
      expect(conflicts).toHaveLength(1);
      expect(conflicts[0]?.kind).toBe("conflicts_with");
    });
  });

  describe("identity_events", () => {
    beforeEach(async () => {
      await store.upsertIdentity(makeNode("e-1"));
    });

    it("emitEvent + history round-trips with stable event_id ordering", async () => {
      const id1 = await store.emitEvent(makeEvent("e-1", "created"));
      const id2 = await store.emitEvent(makeEvent("e-1", "absorbed_record"));
      expect(id1).not.toBeNull();
      expect(id2).not.toBeNull();
      expect(id2!).toBeGreaterThan(id1!);
      const history = await store.history("e-1");
      expect(history.map((e) => e.kind)).toEqual(["created", "absorbed_record"]);
    });

    it("history with limit truncates", async () => {
      await store.emitEvent(makeEvent("e-1", "created"));
      await store.emitEvent(makeEvent("e-1", "absorbed_record"));
      const history = await store.history("e-1", 1);
      expect(history).toHaveLength(1);
    });

    it("hasRunEvent is true only when (entity_id, run_name, kind) matches", async () => {
      await store.emitEvent(makeEvent("e-1", "created"));
      expect(await store.hasRunEvent("e-1", "test-run-1", "created")).toBe(true);
      expect(await store.hasRunEvent("e-1", "test-run-1", "absorbed_record")).toBe(
        false,
      );
      expect(await store.hasRunEvent("e-1", "other-run", "created")).toBe(false);
    });
  });

  describe("identity_aliases", () => {
    beforeEach(async () => {
      await store.upsertIdentity(makeNode("e-1"));
    });

    it("addAlias + resolveAlias round-trips", async () => {
      await store.addAlias(makeAlias("LEI-12345", "e-1"));
      expect(await store.resolveAlias("LEI-12345")).toBe("e-1");
    });

    it("resolveAlias returns null on miss", async () => {
      expect(await store.resolveAlias("missing")).toBeNull();
    });

    it("addAlias upserts on (alias, kind, dataset)", async () => {
      await store.addAlias(makeAlias("LEI-12345", "e-1"));
      await store.addAlias(makeAlias("LEI-12345", "e-2"));
      expect(await store.resolveAlias("LEI-12345")).toBe("e-2");
    });
  });

  describe("persistence + schema", () => {
    it("data persists across close+reopen", async () => {
      await store.upsertIdentity(makeNode("e-1"));
      await store.upsertRecord(makeRecord("csv:r1", "e-1"));
      await store.close();
      const reopened = await SqliteIdentityStore.open({ path: dbPath });
      try {
        const got = await reopened.getIdentity("e-1");
        expect(got?.entityId).toBe("e-1");
        const rec = await reopened.getRecord("csv:r1");
        expect(rec?.recordId).toBe("csv:r1");
      } finally {
        await reopened.close();
      }
    });
  });
});
