import { describe, it, expect } from "vitest";
import { AGENT_CARD, dispatchAnySkill } from "../../src/node/a2a/server.js";

describe("A2A skill parity", () => {
  const byId = new Map(AGENT_CARD.skills.map((s) => [s.id, s]));

  it("every card skill has a non-empty id and human name", () => {
    for (const s of AGENT_CARD.skills) {
      expect(typeof s.id).toBe("string");
      expect(s.id.length).toBeGreaterThan(0);
      expect(typeof s.name).toBe("string");
      expect(s.name.length).toBeGreaterThan(0);
    }
  });

  it("all skill ids are unique", () => {
    const ids = AGENT_CARD.skills.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("advertises canonical ids, not the legacy aliases", () => {
    expect(byId.has("deduplicate")).toBe(true);
    expect(byId.has("explain")).toBe(true);
    expect(byId.has("dedupe")).toBe(false);
    expect(byId.has("explain_pair")).toBe(false);
  });

  it("dispatches the legacy id identically to the canonical id", async () => {
    const rows = [
      { id: "1", name: "Alice", email: "a@x.com" },
      { id: "2", name: "Alice", email: "a@x.com" },
    ];
    expect(await dispatchAnySkill("deduplicate", { rows })).toEqual(
      await dispatchAnySkill("dedupe", { rows }),
    );
    // explain_pair requires a `fields` array (server.ts:375-376); supply it or both throw.
    const pair = {
      row_a: { name: "Jon" },
      row_b: { name: "John" },
      fields: [{ field: "name", scorer: "jaro_winkler", weight: 1 }],
    };
    expect(await dispatchAnySkill("explain", pair)).toEqual(
      await dispatchAnySkill("explain_pair", pair),
    );
  });

  it("humanizes derived ids into labels", () => {
    expect(byId.get("agent_deduplicate")?.name).toBe("Agent Deduplicate");
    expect(byId.get("identity_resolve")?.name).toBe("Identity Resolve");
  });
});
