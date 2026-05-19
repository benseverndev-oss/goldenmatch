/**
 * Identity Graph REST API tests.
 *
 * Binds an in-memory IdentityStore via setServerIdentityStore (so we don't
 * need a real .goldenmatch/identity.db on disk for these tests) and
 * exercises the GET / POST routes.
 */
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import type { Server } from "node:http";

import {
  InMemoryIdentityStore,
  type EvidenceEdge,
  type IdentityNode,
} from "../../src/core/identity/index.js";
import {
  setServerIdentityStore,
  startApiServer,
} from "../../src/node/api/server.js";

const store = new InMemoryIdentityStore();
let server: Server;
let baseUrl: string;

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
  await store.upsertIdentity(makeNode("e-2"));
  await store.upsertRecord({
    recordId: "csv:r1",
    source: "csv:test",
    sourcePk: "r1",
    recordHash: "hash-r1",
    entityId: "e-1",
    payload: null,
    dataset: "test",
    firstSeenAt: new Date(),
    lastSeenAt: new Date(),
  });
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

  setServerIdentityStore(store);
  server = startApiServer({ port: 0, host: "127.0.0.1" });
  await new Promise<void>((r) => {
    if (server.listening) r();
    else server.once("listening", () => r());
  });
  const addr = server.address();
  const port =
    typeof addr === "object" && addr !== null && "port" in addr ? addr.port : 8000;
  baseUrl = `http://127.0.0.1:${port}`;
});

afterAll(async () => {
  setServerIdentityStore(null);
  if (server) {
    await new Promise<void>((r, j) => {
      server.close((err) => (err ? j(err) : r()));
    });
  }
});

describe("REST API /identities", () => {
  it("GET /identities returns items + paging", async () => {
    const res = await fetch(`${baseUrl}/identities?limit=10`);
    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      items: IdentityNode[];
      limit: number;
      offset: number;
    };
    expect(body.items.length).toBe(2);
    expect(body.limit).toBe(10);
  });

  it("GET /identities?dataset filters", async () => {
    const res = await fetch(`${baseUrl}/identities?dataset=test`);
    const body = (await res.json()) as { items: IdentityNode[] };
    expect(body.items.length).toBe(2);
  });

  it("GET /identities/:id returns node + records + edges + events", async () => {
    const res = await fetch(`${baseUrl}/identities/e-1`);
    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      node: IdentityNode;
      records: Array<{ recordId: string }>;
      edges: Array<{ kind: string }>;
      events: unknown[];
    };
    expect(body.node.entityId).toBe("e-1");
    expect(body.records.length).toBe(1);
    expect(body.records[0]?.recordId).toBe("csv:r1");
    expect(body.edges.length).toBe(1);
  });

  it("GET /identities/:id 404s on missing", async () => {
    const res = await fetch(`${baseUrl}/identities/missing`);
    expect(res.status).toBe(404);
  });

  it("GET /identities/conflicts returns conflict edges", async () => {
    const res = await fetch(`${baseUrl}/identities/conflicts`);
    expect(res.status).toBe(200);
    const body = (await res.json()) as { conflicts: Array<{ kind: string }> };
    expect(body.conflicts.length).toBe(1);
    expect(body.conflicts[0]?.kind).toBe("conflicts_with");
  });

  it("GET /identities/:id/history returns events", async () => {
    // Emit an event so history is non-empty.
    await store.emitEvent({
      eventId: null,
      entityId: "e-1",
      kind: "created",
      payload: null,
      runName: "test-run",
      dataset: "test",
      recordedAt: new Date(),
    });
    const res = await fetch(`${baseUrl}/identities/e-1/history`);
    expect(res.status).toBe(200);
    const body = (await res.json()) as { events: unknown[] };
    expect(body.events.length).toBeGreaterThanOrEqual(1);
  });

  it("POST /identities/merge calls manualMerge", async () => {
    const res = await fetch(`${baseUrl}/identities/merge`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keep: "e-1", absorb: "e-2" }),
    });
    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      keep: string;
      absorbed: string;
    };
    expect(body.keep).toBe("e-1");
    expect(body.absorbed).toBe("e-2");
  });

  it("POST /identities/split calls manualSplit", async () => {
    // Re-create state for split — set up a fresh entity with members.
    await store.upsertIdentity(makeNode("e-3"));
    await store.upsertRecord({
      recordId: "csv:r3",
      source: "csv:test",
      sourcePk: "r3",
      recordHash: "hash-r3",
      entityId: "e-3",
      payload: null,
      dataset: "test",
      firstSeenAt: new Date(),
      lastSeenAt: new Date(),
    });
    const res = await fetch(`${baseUrl}/identities/split`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entity_id: "e-3", record_ids: ["csv:r3"] }),
    });
    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      newEntityId: string;
      moved: string[];
    };
    expect(typeof body.newEntityId).toBe("string");
    expect(body.moved).toEqual(["csv:r3"]);
  });

  it("returns 503 when store is not bound", async () => {
    setServerIdentityStore(null);
    const res = await fetch(`${baseUrl}/identities`);
    expect(res.status).toBe(503);
    // Restore for any subsequent tests
    setServerIdentityStore(store);
  });
});
