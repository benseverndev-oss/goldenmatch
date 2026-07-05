import { describe, it, expect } from "vitest";
import { AGENT_CARD } from "../../src/node/a2a/server.js";

describe("A2A agent card — union of registries", () => {
  const ids = new Set(AGENT_CARD.skills.map((s) => s.id));

  it("includes a base A2A skill", () => {
    expect(ids.has("deduplicate")).toBe(true);
  });

  it("includes an agent skill (analyze_data)", () => {
    expect(ids.has("analyze_data")).toBe(true);
  });

  it("includes a memory tool id", () => {
    expect(ids.has("list_corrections")).toBe(true);
  });

  it("includes an identity tool id", () => {
    expect(ids.has("identity_resolve")).toBe(true);
  });

  it("advertises bearer authentication", () => {
    expect(AGENT_CARD.authentication.schemes).toContain("bearer");
  });

  it("does not advertise streaming", () => {
    expect(AGENT_CARD.capabilities["streaming"]).toBe(false);
  });

  it("de-dups skills by id (no duplicate ids)", () => {
    expect(ids.size).toBe(AGENT_CARD.skills.length);
  });

  it("every skill keeps the AgentSkill shape", () => {
    for (const skill of AGENT_CARD.skills) {
      expect(typeof skill.id).toBe("string");
      expect(skill.id.length).toBeGreaterThan(0);
      expect(typeof skill.name).toBe("string");
      expect(skill.name.length).toBeGreaterThan(0);
      expect(typeof skill.description).toBe("string");
      expect(Array.isArray(skill.inputModes)).toBe(true);
      expect(skill.inputModes.length).toBeGreaterThan(0);
      expect(Array.isArray(skill.outputModes)).toBe(true);
      expect(skill.outputModes.length).toBeGreaterThan(0);
    }
  });
});
