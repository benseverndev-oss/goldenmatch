import { describe, it, expect } from "vitest";
import { AGENT_SKILLS, dispatchSkill } from "../../src/core/agent/skills.js";
import { AgentSession } from "../../src/core/agent/session.js";
import { InMemoryStore } from "../../src/core/memory/store.js";
import type { Row } from "../../src/core/types.js";

const ctx = () => ({
  session: new AgentSession(),
  loadTable: async (): Promise<Row[]> => [{ id: "1", name: "a" }],
});

describe("dispatchSkill", () => {
  it("accepts inline rows (edge path, no loadTable call)", async () => {
    let loaded = false;
    const out = await dispatchSkill(
      "analyze_data",
      {
        rows: [
          { id: "1", name: "Alice" },
          { id: "2", name: "Alyce" },
        ],
      },
      {
        session: new AgentSession(),
        loadTable: async () => {
          loaded = true;
          return [];
        },
      },
    );
    expect(loaded).toBe(false);
    expect(out.strategy).toBeDefined();
  });

  it("falls back to loadTable when only file_path given", async () => {
    const out = await dispatchSkill("analyze_data", { file_path: "x.csv" }, ctx());
    expect(out.strategy).toBeDefined();
  });

  it("returns {error} on handler throw", async () => {
    const out = await dispatchSkill(
      "analyze_data",
      {},
      {
        session: new AgentSession(),
        loadTable: async () => {
          throw new Error("no loader");
        },
      },
    );
    expect(out.error).toMatch(/no loader/);
  });

  it("returns {error} for an unknown skill id", async () => {
    const out = await dispatchSkill("does_not_exist", {}, ctx());
    expect(out.error).toMatch(/unknown skill/i);
  });

  it("auto_configure returns a config + telemetry", async () => {
    const out = await dispatchSkill(
      "auto_configure",
      {
        rows: [
          { id: "1", name: "John Smith" },
          { id: "2", name: "Jon Smith" },
          { id: "3", name: "Mary Jones" },
        ],
      },
      ctx(),
    );
    expect(out.config).toBeDefined();
    expect(out.telemetry).toBeDefined();
  });

  it("agent_deduplicate returns confidence_distribution", async () => {
    const out = await dispatchSkill(
      "agent_deduplicate",
      {
        rows: [
          { id: "1", name: "John Smith" },
          { id: "2", name: "Jon Smith" },
          { id: "3", name: "Mary Jones" },
        ],
      },
      ctx(),
    );
    expect(out.confidence_distribution).toBeDefined();
    expect(out.storage).toBe("memory");
  });

  it("suggest_pprl reports availability", async () => {
    const out = await dispatchSkill(
      "suggest_pprl",
      { rows: [{ ssn: "111-22-3333" }] },
      ctx(),
    );
    expect(out.has_sensitive).toBe(true);
  });

  it("every skill has id + description + inputSchema + handler", () => {
    for (const s of AGENT_SKILLS) {
      expect(s.id).toBeTruthy();
      expect(s.description).toBeTruthy();
      expect(s.inputSchema).toBeTruthy();
      expect(typeof s.handler).toBe("function");
    }
  });

  it("registers the full Wave-2 skill set + healer review_config (15 skills)", () => {
    expect(AGENT_SKILLS.length).toBe(15);
    const ids = new Set(AGENT_SKILLS.map((s) => s.id));
    for (const expected of [
      "agent_explain_pair",
      "agent_explain_cluster",
      "controller_telemetry",
      "agent_review_queue",
      "agent_approve_reject",
      "scan_quality",
      "fix_quality",
      "run_transforms",
      "review_config",
    ]) {
      expect(ids.has(expected)).toBe(true);
    }
  });

  it("agent_explain_pair returns a score + explanation", async () => {
    const out = await dispatchSkill(
      "agent_explain_pair",
      {
        record_a: { name: "John Smith", city: "Boston" },
        record_b: { name: "Jon Smith", city: "Boston" },
        fuzzy: { name: 1.0 },
        exact: ["city"],
      },
      ctx(),
    );
    expect(typeof out.score).toBe("number");
    expect(out.explanation).toBeDefined();
    expect(out.confidence).toBeDefined();
  });

  it("agent_explain_pair defaults to shared keys when no fuzzy/exact", async () => {
    const out = await dispatchSkill(
      "agent_explain_pair",
      {
        record_a: { name: "Alice", town: "Reno" },
        record_b: { name: "Alyce", town: "Reno" },
      },
      ctx(),
    );
    expect(typeof out.score).toBe("number");
  });

  it("agent_explain_cluster is declarative (stateless note)", async () => {
    const out = await dispatchSkill(
      "agent_explain_cluster",
      { cluster_id: 7 },
      ctx(),
    );
    expect(out.cluster_id).toBe(7);
    expect(out.note).toBeDefined();
  });

  it("controller_telemetry is declarative (available:false)", async () => {
    const out = await dispatchSkill("controller_telemetry", {}, ctx());
    expect(out.available).toBe(false);
    expect(out.note).toBeDefined();
  });

  it("agent_review_queue returns the needs-review pending list", async () => {
    const out = await dispatchSkill(
      "agent_review_queue",
      {
        rows: [
          { id: "1", name: "John Smith" },
          { id: "2", name: "Jon Smith" },
          { id: "3", name: "Mary Jones" },
        ],
      },
      ctx(),
    );
    expect(Array.isArray(out.pending)).toBe(true);
    expect(typeof out.count).toBe("number");
  });

  it("agent_approve_reject returns Python-shaped status on the edge path (no store)", async () => {
    const out = await dispatchSkill(
      "agent_approve_reject",
      { id_a: 1, id_b: 2, decision: "approve", decided_by: "tester", job_name: "j" },
      ctx(),
    );
    // No memory store wired -> not persisted, but the decision is still
    // returned (mirrors Python's memory_store=None branch).
    expect(out.status).toBe("ok");
    expect(out.decision).toBe("approve");
    expect(out.id_a).toBe(1);
    expect(out.id_b).toBe(2);
    expect(out.decided_by).toBe("tester");
    expect(out.job_name).toBe("j");
  });

  it("agent_approve_reject persists an approve correction (canonicalized pair)", async () => {
    const store = new InMemoryStore();
    const out = await dispatchSkill(
      "agent_approve_reject",
      // deliberately unordered ids -> stored as (min, max)
      { id_a: 5, id_b: 2, decision: "approve", decided_by: "tester" },
      { ...ctx(), openMemoryStore: async () => store },
    );
    expect(out.status).toBe("ok");
    const corrections = await store.getCorrections();
    expect(corrections).toHaveLength(1);
    const c = corrections[0]!;
    expect(c.idA).toBe(2);
    expect(c.idB).toBe(5);
    expect(c.decision).toBe("approve");
    expect(c.source).toBe("agent");
    expect(c.trust).toBe(0.5);
    expect(c.fieldHash).toBe("");
    expect(c.recordHash).toBe("");
    expect(c.originalScore).toBe(0);
  });

  it("agent_approve_reject persists a reject correction with reason + dataset", async () => {
    const store = new InMemoryStore();
    const out = await dispatchSkill(
      "agent_approve_reject",
      {
        id_a: 7,
        id_b: 3,
        decision: "reject",
        decided_by: "tester",
        reason: "different people",
        dataset: "voters.csv",
      },
      { ...ctx(), openMemoryStore: async () => store },
    );
    expect(out.status).toBe("ok");
    expect(out.decision).toBe("reject");
    const corrections = await store.getCorrections();
    expect(corrections).toHaveLength(1);
    const c = corrections[0]!;
    expect(c.idA).toBe(3);
    expect(c.idB).toBe(7);
    expect(c.decision).toBe("reject");
    expect(c.source).toBe("agent");
    expect(c.trust).toBe(0.5);
    expect(c.reason).toBe("different people");
    expect(c.dataset).toBe("voters.csv");
  });

  it("agent_approve_reject does not persist an invalid decision", async () => {
    const store = new InMemoryStore();
    const out = await dispatchSkill(
      "agent_approve_reject",
      { id_a: 1, id_b: 2, decision: "maybe" },
      { ...ctx(), openMemoryStore: async () => store },
    );
    expect(out.error).toMatch(/invalid decision/i);
    expect(await store.countCorrections()).toBe(0);
  });

  it("scan_quality fails open when goldencheck is absent", async () => {
    const out = await dispatchSkill(
      "scan_quality",
      { rows: [{ id: "1", name: "a" }] },
      ctx(),
    );
    expect(out.error).toMatch(/goldencheck not installed/);
  });

  it("fix_quality fails open when goldencheck is absent", async () => {
    const out = await dispatchSkill(
      "fix_quality",
      { rows: [{ id: "1", name: "a" }] },
      ctx(),
    );
    expect(out.error).toMatch(/goldencheck not installed/);
  });

  it("run_transforms fails open when goldenflow is absent", async () => {
    const out = await dispatchSkill(
      "run_transforms",
      { rows: [{ id: "1", name: "a" }] },
      ctx(),
    );
    expect(out.error).toMatch(/goldenflow not installed/);
  });
});
