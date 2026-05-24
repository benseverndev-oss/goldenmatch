/**
 * MCP identity-tools tests. Mirrors the Python
 * tests/test_mcp_identity_tools.py surface (resolve/list/history/conflicts/
 * merge/split) but injects an InMemoryIdentityStore via the test seam so it
 * runs without the better-sqlite3 peer dep.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";

import {
  IDENTITY_TOOLS,
  IDENTITY_TOOL_NAMES,
  handleIdentityTool,
  __setIdentityStoreFactoryForTests,
} from "../../src/node/mcp/identity-tools.js";
import { InMemoryIdentityStore } from "../../src/core/identity/in-memory-store.js";
import type { IdentityStore } from "../../src/core/identity/types.js";

const NOW = new Date("2026-01-01T00:00:00.000Z");

async function seed(): Promise<IdentityStore> {
  const store = new InMemoryIdentityStore();
  for (const entityId of ["E1", "E2"]) {
    await store.upsertIdentity({
      entityId,
      status: "active",
      mergedInto: null,
      goldenRecord: { name: entityId },
      confidence: 0.9,
      dataset: "d",
      createdAt: NOW,
      updatedAt: NOW,
    });
  }
  await store.upsertRecord({
    recordId: "src:1",
    source: "src",
    sourcePk: "1",
    recordHash: "h1",
    entityId: "E1",
    payload: { name: "Alice" },
    dataset: "d",
    firstSeenAt: NOW,
    lastSeenAt: NOW,
  });
  await store.upsertRecord({
    recordId: "src:2",
    source: "src",
    sourcePk: "2",
    recordHash: "h2",
    entityId: "E2",
    payload: { name: "Alicia" },
    dataset: "d",
    firstSeenAt: NOW,
    lastSeenAt: NOW,
  });
  await store.emitEvent({
    eventId: null,
    entityId: "E1",
    kind: "created",
    payload: {},
    runName: "r",
    dataset: "d",
    recordedAt: NOW,
  });
  await store.addEdge({
    edgeId: null,
    entityId: "E1",
    recordAId: "src:1",
    recordBId: "src:2",
    kind: "conflicts_with",
    score: 0.4,
    matchkeyName: "name",
    fieldScores: null,
    negativeEvidence: null,
    controllerSnapshot: null,
    runName: "r",
    dataset: "d",
    recordedAt: NOW,
  });
  return store;
}

async function call(name: string, args: Record<string, unknown>): Promise<Record<string, unknown>> {
  const content = await handleIdentityTool(name, args);
  return JSON.parse(content[0]!.text) as Record<string, unknown>;
}

let store: IdentityStore;

beforeEach(async () => {
  store = await seed();
  __setIdentityStoreFactoryForTests(async () => store);
});

afterEach(() => {
  __setIdentityStoreFactoryForTests(null);
});

describe("IDENTITY_TOOLS metadata", () => {
  it("exports the 6 identity tools matching the Python sibling", () => {
    expect(IDENTITY_TOOLS.map((t) => t.name)).toEqual([
      "identity_resolve",
      "identity_list",
      "identity_history",
      "identity_conflicts",
      "identity_merge",
      "identity_split",
    ]);
    expect(IDENTITY_TOOL_NAMES.size).toBe(6);
    for (const t of IDENTITY_TOOLS) {
      expect(t.description.length).toBeGreaterThan(0);
      expect(t.inputSchema).toBeTypeOf("object");
    }
  });
});

describe("identity tool dispatch", () => {
  it("identity_resolve returns the full snake_case view", async () => {
    const r = await call("identity_resolve", { record_id: "src:1" });
    const node = r["node"] as Record<string, unknown>;
    expect(node["entity_id"]).toBe("E1");
    expect((r["records"] as unknown[]).length).toBe(1);
    expect((r["records"] as Record<string, unknown>[])[0]!["record_id"]).toBe("src:1");
  });

  it("identity_resolve returns { found: false } for an unknown record", async () => {
    const r = await call("identity_resolve", { record_id: "nope:0" });
    expect(r["found"]).toBe(false);
  });

  it("identity_resolve errors without record_id", async () => {
    const r = await call("identity_resolve", {});
    expect(r["error"]).toBeTypeOf("string");
  });

  it("identity_list returns snake_case nodes", async () => {
    const r = await call("identity_list", {});
    const items = r["items"] as Record<string, unknown>[];
    expect(items.map((i) => i["entity_id"]).sort()).toEqual(["E1", "E2"]);
  });

  it("identity_history returns the event log", async () => {
    const r = await call("identity_history", { entity_id: "E1" });
    const items = r["items"] as Record<string, unknown>[];
    expect(items.some((e) => e["kind"] === "created")).toBe(true);
  });

  it("identity_conflicts returns conflicts_with edges", async () => {
    const r = await call("identity_conflicts", {});
    const items = r["items"] as Record<string, unknown>[];
    expect(items.length).toBe(1);
    expect(items[0]!["kind"]).toBe("conflicts_with");
    expect(items[0]!["record_a_id"]).toBe("src:1");
  });

  it("identity_merge reassigns the absorbed entity's records", async () => {
    const r = await call("identity_merge", {
      keep_entity_id: "E1",
      absorb_entity_id: "E2",
      reason: "dupe",
    });
    expect(r["keep"]).toBe("E1");
    expect(r["absorbed"]).toBe("E2");
    // src:2 now resolves to E1.
    const resolved = await call("identity_resolve", { record_id: "src:2" });
    expect((resolved["node"] as Record<string, unknown>)["entity_id"]).toBe("E1");
  });

  it("identity_split moves records to a fresh identity", async () => {
    const r = await call("identity_split", {
      entity_id: "E1",
      record_ids: ["src:1"],
      reason: "wrong merge",
    });
    expect(r["new_entity_id"]).toBeTypeOf("string");
    expect(r["moved"]).toEqual(["src:1"]);
    const resolved = await call("identity_resolve", { record_id: "src:1" });
    expect((resolved["node"] as Record<string, unknown>)["entity_id"]).toBe(r["new_entity_id"]);
  });
});
