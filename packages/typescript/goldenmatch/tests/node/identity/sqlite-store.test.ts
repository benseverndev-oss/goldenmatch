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
  AuditSeal,
  EvidenceEdge,
  IdentityAlias,
  IdentityEvent,
  IdentityNode,
  SourceRecord,
} from "../../../src/core/identity/types.js";
import { SqliteIdentityStore } from "../../../src/node/identity/sqlite-store.js";
import { claimRecord } from "../../../src/core/identity/query.js";
import { mediateConflict, openConflicts } from "../../../src/core/identity/mediation.js";

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

function makeSeal(overrides: Partial<AuditSeal> = {}): AuditSeal {
  return {
    sealId: null,
    rootHash: "root-abc",
    eventCount: 3,
    lastEventId: 3,
    dataset: null,
    prevSealId: null,
    prevRoot: null,
    actor: "steward:sam",
    createdAt: new Date(),
    ...overrides,
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function openRawDb(path: string): Promise<any> {
  const mod = await import("better-sqlite3" as string);
  const Database = (mod as { default?: unknown }).default ?? mod;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return new (Database as any)(path);
}

// The evidence_edges + identity_events shape at schema v2 (before the v5
// provenance/audit/claim columns) -- used to synthesize a legacy DB the
// migration must upgrade in place.
const V2_SCHEMA = `
  CREATE TABLE evidence_edges (
    edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL, record_a_id TEXT NOT NULL, record_b_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'same_as', score REAL, matchkey_name TEXT,
    field_scores TEXT, negative_evidence TEXT, controller_snapshot TEXT,
    run_name TEXT, dataset TEXT,
    recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_id, record_a_id, record_b_id, kind, run_name)
  );
  CREATE TABLE identity_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT NOT NULL,
    kind TEXT NOT NULL, payload TEXT, run_name TEXT, dataset TEXT,
    recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
  );
`;

function columnSet(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  db: any,
  table: string,
): Set<string> {
  return new Set(
    (db.prepare(`PRAGMA table_info(${table})`).all() as { name: string }[]).map(
      (r) => r.name,
    ),
  );
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

    it("edgesByKind returns edges of a given kind, newest-first", async () => {
      await store.addEdge(makeEdge("e-1", "csv:r1", "csv:r2"));
      const v1: EvidenceEdge = {
        ...makeEdge("e-1", "csv:r1", "csv:r3"),
        kind: "mediation_verdict",
        negativeEvidence: { resolution: "defer" },
        runName: "mediation:1",
        recordedAt: new Date("2026-01-01T00:00:00Z"),
      };
      const v2: EvidenceEdge = {
        ...makeEdge("e-1", "csv:r1", "csv:r3"),
        kind: "mediation_verdict",
        negativeEvidence: { resolution: "distinct" },
        runName: "mediation:2",
        recordedAt: new Date("2026-01-02T00:00:00Z"),
      };
      await store.addEdge(v1);
      await store.addEdge(v2);
      const verdicts = await store.edgesByKind("mediation_verdict");
      expect(verdicts).toHaveLength(2);
      // newest-first: distinct (2026-01-02) before defer (2026-01-01)
      expect(verdicts[0]?.negativeEvidence?.["resolution"]).toBe("distinct");
      // no same_as leakage
      expect(verdicts.every((e) => e.kind === "mediation_verdict")).toBe(true);
    });
  });

  describe("mediation + claim (durable backend)", () => {
    it("openConflicts closes after a terminal mediateConflict verdict", async () => {
      await store.upsertIdentity(makeNode("e-1"));
      await store.upsertRecord(makeRecord("csv:r1", "e-1"));
      await store.upsertRecord(makeRecord("csv:r2", "e-1"));
      await store.addEdge({
        ...makeEdge("e-1", "csv:r1", "csv:r2"),
        kind: "conflicts_with",
      });
      const before = await openConflicts(store);
      expect(before).toHaveLength(1);

      const res = await mediateConflict(store, "csv:r1", "csv:r2", "distinct");
      expect(res.action.type).toBe("split");
      // csv:r2 split off e-1.
      expect((await store.getRecord("csv:r2"))?.entityId).not.toBe("e-1");
      // conflict now closed.
      expect(await openConflicts(store)).toHaveLength(0);
    });

    it("re-mediating the same pair is NOT a silent no-op", async () => {
      await store.upsertIdentity(makeNode("e-1"));
      await store.upsertRecord(makeRecord("csv:r1", "e-1"));
      await store.upsertRecord(makeRecord("csv:r2", "e-1"));
      await store.addEdge({
        ...makeEdge("e-1", "csv:r1", "csv:r2"),
        kind: "conflicts_with",
      });
      await mediateConflict(store, "csv:r1", "csv:r2", "defer");
      // deferred conflict is still open.
      expect(await openConflicts(store)).toHaveLength(1);
      // re-mediate as distinct -> a SECOND verdict edge, conflict closes.
      await mediateConflict(store, "csv:r1", "csv:r2", "distinct");
      expect(await store.edgesByKind("mediation_verdict")).toHaveLength(2);
      expect(await openConflicts(store)).toHaveLength(0);
    });

    it("claimRecord reassigns a record and emits claimed on both entities", async () => {
      await store.upsertIdentity(makeNode("e-1"));
      await store.upsertIdentity(makeNode("e-2"));
      await store.upsertRecord(makeRecord("csv:r1", "e-2"));
      const res = await claimRecord(store, "e-1", "csv:r1");
      expect(res.moved).toBe(true);
      expect(res.fromEntity).toBe("e-2");
      expect((await store.getRecord("csv:r1"))?.entityId).toBe("e-1");
      expect((await store.history("e-1")).some((e) => e.kind === "claimed")).toBe(true);
      expect((await store.history("e-2")).some((e) => e.kind === "claimed")).toBe(true);
      // replay is a no-op.
      const replay = await claimRecord(store, "e-1", "csv:r1");
      expect(replay.moved).toBe(false);
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

  describe("v5 provenance + audit schema", () => {
    it("provenance (actor/trust) round-trips on events + edges", async () => {
      await store.upsertIdentity(makeNode("e-1"));
      await store.emitEvent({
        ...makeEvent("e-1", "created"),
        actor: "agent",
        trust: 0.5,
        claimType: "inference",
        evidenceRef: "tool-call",
        previousClaimId: null,
      });
      const [ev] = await store.history("e-1");
      expect(ev?.actor).toBe("agent");
      expect(ev?.trust).toBe(0.5);
      expect(ev?.claimType).toBe("inference");
      expect(ev?.evidenceRef).toBe("tool-call");

      await store.addEdge({
        ...makeEdge("e-1", "csv:r1", "csv:r2"),
        actor: "steward:sam",
        trust: 1.0,
      });
      const [edge] = await store.edgesForEntity("e-1");
      expect(edge?.actor).toBe("steward:sam");
      expect(edge?.trust).toBe(1.0);
    });

    it("a provenance-free event reads back with null actor/trust", async () => {
      await store.upsertIdentity(makeNode("e-1"));
      await store.emitEvent(makeEvent("e-1", "created")); // no actor/trust set
      const [ev] = await store.history("e-1");
      expect(ev?.actor).toBeNull();
      expect(ev?.trust).toBeNull();
      expect(ev?.claimType).toBeNull();
      // PR-B: emitEvent now stamps a tamper-evidence content hash at insert, so
      // entryHash is a 64-char sha256 hex (no longer null).
      expect(ev?.entryHash).toMatch(/^[0-9a-f]{64}$/);
    });

    it("audit_seals CRUD: addSeal / latestSeal / listSeals with dataset scoping", async () => {
      // Two seals on the global (dataset IS NULL) chain, chained.
      const id1 = await store.addSeal(makeSeal({ rootHash: "r1", eventCount: 2, lastEventId: 2 }));
      expect(typeof id1).toBe("number");
      const id2 = await store.addSeal(
        makeSeal({ rootHash: "r2", eventCount: 5, lastEventId: 5, prevSealId: id1, prevRoot: "r1" }),
      );
      // One seal on a named dataset chain.
      await store.addSeal(makeSeal({ dataset: "d1", rootHash: "d1-root", eventCount: 1, lastEventId: 1 }));

      const globalLatest = await store.latestSeal();
      expect(globalLatest?.sealId).toBe(id2);
      expect(globalLatest?.rootHash).toBe("r2");
      expect(globalLatest?.prevSealId).toBe(id1);
      expect(globalLatest?.dataset).toBeNull();

      const globalSeals = await store.listSeals();
      expect(globalSeals.map((s) => s.rootHash)).toEqual(["r1", "r2"]);
      expect(globalSeals.every((s) => s.dataset === null)).toBe(true);

      const d1Latest = await store.latestSeal("d1");
      expect(d1Latest?.rootHash).toBe("d1-root");
      // The named-dataset scope excludes the global chain.
      expect((await store.listSeals("d1")).map((s) => s.rootHash)).toEqual(["d1-root"]);
    });

    it("exportAuditLog returns events in event_id order, dataset-scoped", async () => {
      await store.upsertIdentity(makeNode("e-1"));
      await store.upsertIdentity(makeNode("e-2", "other"));
      await store.emitEvent({ ...makeEvent("e-1", "created"), dataset: "test" });
      await store.emitEvent({ ...makeEvent("e-1", "absorbed_record"), dataset: "test" });
      await store.emitEvent({ ...makeEvent("e-2", "created"), dataset: "other" });

      const all = await store.exportAuditLog();
      expect(all.map((e) => e.kind)).toEqual(["created", "absorbed_record", "created"]);
      // event_id strictly ascending (commit order).
      for (let i = 1; i < all.length; i++) {
        expect(all[i]!.eventId!).toBeGreaterThan(all[i - 1]!.eventId!);
      }
      const scoped = await store.exportAuditLog("test");
      expect(scoped).toHaveLength(2);
      expect(scoped.every((e) => e.dataset === "test")).toBe(true);
    });
  });

  describe("v2 -> v5 migration", () => {
    it("migrates a synthesized v2 DB in place, adding the new columns", async () => {
      const v2Path = join(tmpDir, "v2.db");
      const raw = await openRawDb(v2Path);
      raw.exec(V2_SCHEMA);
      raw.pragma("user_version = 2");
      // A legacy event + edge written before the provenance columns existed.
      raw
        .prepare(
          "INSERT INTO identity_events (entity_id, kind, payload, run_name, dataset, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
        )
        .run("e-1", "created", null, "r", "test", "2026-01-01T00:00:00");
      raw
        .prepare(
          "INSERT INTO evidence_edges (entity_id, record_a_id, record_b_id, kind, run_name, dataset, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        )
        .run("e-1", "csv:r1", "csv:r2", "same_as", "r", "test", "2026-01-01T00:00:00");
      raw.close();

      const migrated = await SqliteIdentityStore.open({ path: v2Path });
      try {
        // New event that USES the added columns -- would throw "no column named
        // actor" if the migration hadn't added them.
        await migrated.emitEvent({
          ...makeEvent("e-1", "absorbed_record"),
          actor: "agent",
          trust: 0.5,
        });
        const events = await migrated.history("e-1");
        expect(events).toHaveLength(2);
        // The legacy row reads back with null provenance; the new row keeps it.
        const legacy = events.find((e) => e.kind === "created");
        const fresh = events.find((e) => e.kind === "absorbed_record");
        expect(legacy?.actor).toBeNull();
        expect(fresh?.actor).toBe("agent");
        expect(fresh?.trust).toBe(0.5);

        // The audit_seals table was created by the migration/schema.
        const sid = await migrated.addSeal(makeSeal());
        expect(typeof sid).toBe("number");
        expect((await migrated.latestSeal())?.rootHash).toBe("root-abc");
      } finally {
        await migrated.close();
      }

      // user_version bumped to 5, and both tables carry the new columns.
      const check = await openRawDb(v2Path);
      try {
        expect((check.pragma("user_version", { simple: true }) as number)).toBe(5);
        const evCols = columnSet(check, "identity_events");
        for (const c of ["actor", "trust", "claim_type", "evidence_ref", "previous_claim_id", "entry_hash"]) {
          expect(evCols.has(c)).toBe(true);
        }
        const edgeCols = columnSet(check, "evidence_edges");
        expect(edgeCols.has("actor")).toBe(true);
        expect(edgeCols.has("trust")).toBe(true);
      } finally {
        check.close();
      }
    });

    it("opens a v5 DB unchanged -- reopening runs the migration as a no-op", async () => {
      // A fresh store IS v5. Write provenance + a seal, then reopen: migrateToV5
      // probes and finds every column present, so it must NOT re-ALTER (which
      // would throw "duplicate column name") -- the Python-written-v5 case.
      await store.upsertIdentity(makeNode("e-1"));
      await store.emitEvent({ ...makeEvent("e-1", "created"), actor: "agent", trust: 0.5 });
      await store.addSeal(makeSeal({ rootHash: "seal-1" }));
      await store.close();

      const reopened = await SqliteIdentityStore.open({ path: dbPath });
      try {
        const [ev] = await reopened.history("e-1");
        expect(ev?.actor).toBe("agent");
        expect(ev?.trust).toBe(0.5);
        expect((await reopened.latestSeal())?.rootHash).toBe("seal-1");
      } finally {
        await reopened.close();
      }
    });
  });
});
