import { describe, it, expect } from "vitest";
import { AGENT_SKILLS, dispatchSkill } from "../../src/core/agent/skills.js";
import { AgentSession } from "../../src/core/agent/session.js";
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
});
