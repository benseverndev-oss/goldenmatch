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
  it("exports the 17 identity tools matching the Python sibling", () => {
    expect(IDENTITY_TOOLS.map((t) => t.name)).toEqual([
      "identity_resolve",
      "identity_list",
      "identity_history",
      "identity_conflicts",
      "identity_merge",
      "identity_split",
      "identity_claim",
      "identity_resolve_conflict",
      "identity_audit",
      "identity_audit_seal",
      "identity_audit_verify",
      "identity_show",
      "identity_profile",
      "identity_stats",
      "identity_worklist",
      "customer_360",
      "certify_serving_joins",
    ]);
    expect(IDENTITY_TOOL_NAMES.size).toBe(17);
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

  it("identity_claim reassigns a record and reports the losing entity", async () => {
    const r = await call("identity_claim", { entity_id: "E1", record_id: "src:2" });
    expect(r["moved"]).toBe(true);
    expect(r["entity_id"]).toBe("E1");
    expect(r["from_entity"]).toBe("E2");
    const resolved = await call("identity_resolve", { record_id: "src:2" });
    expect((resolved["node"] as Record<string, unknown>)["entity_id"]).toBe("E1");
    // claimed event on BOTH the gaining (E1) and losing (E2) entities.
    const h1 = await call("identity_history", { entity_id: "E1" });
    const h2 = await call("identity_history", { entity_id: "E2" });
    expect((h1["items"] as Record<string, unknown>[]).some((e) => e["kind"] === "claimed")).toBe(true);
    expect((h2["items"] as Record<string, unknown>[]).some((e) => e["kind"] === "claimed")).toBe(true);
  });

  it("identity_claim is a no-op on replay", async () => {
    const r1 = await call("identity_claim", { entity_id: "E1", record_id: "src:2" });
    expect(r1["moved"]).toBe(true);
    const r2 = await call("identity_claim", { entity_id: "E1", record_id: "src:2" });
    expect(r2["moved"]).toBe(false);
  });

  it("identity_resolve_conflict 'distinct' splits record_b out", async () => {
    // src:1 & src:2 both belong to E1 first (claim src:2 into E1), conflict edge exists.
    await call("identity_claim", { entity_id: "E1", record_id: "src:2" });
    const r = await call("identity_resolve_conflict", {
      record_a_id: "src:1",
      record_b_id: "src:2",
      resolution: "distinct",
    });
    expect(r["resolution"]).toBe("distinct");
    const action = r["action"] as Record<string, unknown>;
    expect(action["type"]).toBe("split");
    // src:2 is now on a fresh identity, not E1.
    const resolved = await call("identity_resolve", { record_id: "src:2" });
    expect((resolved["node"] as Record<string, unknown>)["entity_id"]).not.toBe("E1");
  });

  it("identity_resolve_conflict 'same'/'defer' record a verdict without splitting", async () => {
    const same = await call("identity_resolve_conflict", {
      record_a_id: "src:1",
      record_b_id: "src:2",
      resolution: "same",
    });
    expect((same["action"] as Record<string, unknown>)["type"]).toBe("none");
    const defer = await call("identity_resolve_conflict", {
      record_a_id: "src:1",
      record_b_id: "src:2",
      resolution: "defer",
    });
    expect((defer["action"] as Record<string, unknown>)["type"]).toBe("none");
    // A conflict_mediated event landed on the origin entity.
    const h1 = await call("identity_history", { entity_id: "E1" });
    expect(
      (h1["items"] as Record<string, unknown>[]).some((e) => e["kind"] === "conflict_mediated"),
    ).toBe(true);
  });

  it("identity_resolve_conflict rejects an invalid resolution", async () => {
    const r = await call("identity_resolve_conflict", {
      record_a_id: "src:1",
      record_b_id: "src:2",
      resolution: "nonsense",
    });
    expect(r["error"]).toBeTypeOf("string");
  });
});

describe("identity tool provenance (actor/trust)", () => {
  function eventsOf(historyResult: Record<string, unknown>): Record<string, unknown>[] {
    return historyResult["items"] as Record<string, unknown>[];
  }

  it("identity_merge persists an explicit actor/trust on the emitted events", async () => {
    await call("identity_merge", {
      keep_entity_id: "E1",
      absorb_entity_id: "E2",
      actor: "steward:sam",
      trust: 0.95,
    });
    const merge = eventsOf(await call("identity_history", { entity_id: "E1" })).find(
      (e) => e["kind"] === "manual_merge",
    );
    expect(merge).toBeDefined();
    expect(merge!["actor"]).toBe("steward:sam");
    expect(merge!["trust"]).toBe(0.95);
  });

  it("defaults actor='agent' and derives trust=0.5 when omitted", async () => {
    await call("identity_split", { entity_id: "E1", record_ids: ["src:1"] });
    const split = eventsOf(await call("identity_history", { entity_id: "E1" })).find(
      (e) => e["kind"] === "manual_split",
    );
    expect(split!["actor"]).toBe("agent");
    // trustForSource("agent") === 0.5
    expect(split!["trust"]).toBe(0.5);
  });

  it("identity_claim + identity_resolve_conflict stamp actor/trust", async () => {
    await call("identity_claim", {
      entity_id: "E1",
      record_id: "src:2",
      actor: "steward:kim",
      trust: 1.0,
    });
    const claimed = eventsOf(await call("identity_history", { entity_id: "E1" })).find(
      (e) => e["kind"] === "claimed",
    );
    expect(claimed!["actor"]).toBe("steward:kim");
    expect(claimed!["trust"]).toBe(1.0);

    // Now src:1 and src:2 both sit in E1 -> mediate the conflict with provenance.
    await call("identity_resolve_conflict", {
      record_a_id: "src:1",
      record_b_id: "src:2",
      resolution: "same",
      actor: "steward:kim",
      trust: 0.8,
    });
    const mediated = eventsOf(await call("identity_history", { entity_id: "E1" })).find(
      (e) => e["kind"] === "conflict_mediated",
    );
    expect(mediated!["actor"]).toBe("steward:kim");
    expect(mediated!["trust"]).toBe(0.8);
  });
});

describe("identity read tools (show / profile / stats / worklist)", () => {
  it("identity_show returns the full entity view", async () => {
    const r = await call("identity_show", { entity_id: "E1" });
    expect((r["node"] as Record<string, unknown>)["entity_id"]).toBe("E1");
    expect((r["records"] as unknown[]).length).toBe(1);
  });

  it("identity_show returns { found: false } for an unknown entity", async () => {
    const r = await call("identity_show", { entity_id: "NOPE" });
    expect(r["found"]).toBe(false);
  });

  it("identity_profile reports records / conflicts / structural version", async () => {
    const r = await call("identity_profile", { entity_id: "E1" });
    expect(r["record_count"]).toBe(1);
    expect(r["conflict_count"]).toBe(1); // the seeded conflicts_with edge
    expect(r["version"]).toBe(1); // one 'created' structural event
    expect(r["sources"]).toEqual(["src"]);
  });

  it("identity_stats summarizes the graph", async () => {
    const r = await call("identity_stats", {});
    expect(r["total_entities"]).toBe(2);
    expect((r["by_status"] as Record<string, number>)["active"]).toBe(2);
    expect(r["total_conflicts"]).toBe(1);
  });

  it("identity_worklist surfaces the entity with a conflict", async () => {
    const r = await call("identity_worklist", {});
    const items = r["items"] as Record<string, unknown>[];
    expect(items.some((i) => i["entity_id"] === "E1")).toBe(true);
    const e1 = items.find((i) => i["entity_id"] === "E1")!;
    expect(e1["reasons"]).toContain("has_conflicts");
  });

  it("customer_360 composes the unified serving view", async () => {
    const r = await call("customer_360", { entity_id: "E1" });
    expect(r["entity_id"]).toBe("E1");
    expect(r["record_count"]).toBe(1);
    expect(r["golden_record"]).toEqual({ name: "E1" });
    // every 360 section is present + JSON-ready
    for (const key of [
      "field_provenance",
      "source_records",
      "timeline",
      "relationships",
    ]) {
      expect(Array.isArray(r[key])).toBe(true);
    }
    // per-field provenance: the golden `name` (E1) disagrees with src:1's
    // payload (Alice), so it has no winning contributor and records the override
    const prov = r["field_provenance"] as Record<string, unknown>[];
    const nameProv = prov.find((p) => p["field"] === "name")!;
    expect(nameProv["winning_record_id"]).toBeNull();
    const conflicts = nameProv["conflicting_values"] as Record<string, unknown>[];
    expect(conflicts.some((c) => c["value"] === "Alice")).toBe(true);
    // the edge-safe store has no relationship overlay -> empty (Python's fallback)
    expect(r["relationships"]).toEqual([]);
  });

  it("customer_360 attributes a golden field back to the source record that carries it", async () => {
    // Re-stamp E1's golden so it agrees with src:1's payload (name: Alice).
    await store.upsertIdentity({
      entityId: "E1",
      status: "active",
      mergedInto: null,
      goldenRecord: { name: "Alice" },
      confidence: 0.9,
      dataset: "d",
      createdAt: NOW,
      updatedAt: NOW,
    });
    const r = await call("customer_360", { entity_id: "E1" });
    const prov = r["field_provenance"] as Record<string, unknown>[];
    const nameProv = prov.find((p) => p["field"] === "name")!;
    expect(nameProv["winning_record_id"]).toBe("src:1");
    expect(nameProv["winning_source"]).toBe("src");
  });

  it("customer_360 respects include_relationships=false and returns found:false for unknowns", async () => {
    const r = await call("customer_360", { entity_id: "E1", include_relationships: false });
    expect(r["relationships"]).toEqual([]);
    const missing = await call("customer_360", { entity_id: "NOPE" });
    expect(missing["found"]).toBe(false);
  });

  it("certify_serving_joins certifies the record_id join key", async () => {
    // The seed has 2 entities (E1, E2) with distinct src:1 / src:2 -> unique.
    const r = await call("certify_serving_joins", { dataset: "d" });
    expect(r["trustworthy"]).toBe(true);
    expect(r["n_entities"]).toBe(2);
    expect(r["n_records"]).toBe(2);
    expect(r["truncated"]).toBe(false);
    const rc = r["record_id"] as Record<string, unknown>;
    expect(rc["is_unique_at_grain"]).toBe(true);
    expect(rc["duplicate_key_groups"]).toBe(0);
  });
});
