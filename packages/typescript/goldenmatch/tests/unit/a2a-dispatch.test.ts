import { describe, it, expect } from "vitest";
import { dispatchAnySkill } from "../../src/node/a2a/server.js";

describe("A2A unified dispatch — dispatchAnySkill", () => {
  it("routes an agent skill (analyze_data) and returns a strategy", async () => {
    const result = (await dispatchAnySkill("analyze_data", {
      rows: [
        { id: 1, email: "a@x.com", name: "Alice" },
        { id: 2, email: "b@x.com", name: "Bob" },
        { id: 3, email: "c@x.com", name: "Carol" },
      ],
    })) as Record<string, unknown>;
    // dispatchSkill never throws; on success the analyze payload carries a
    // top-level `strategy` string.
    expect(result["error"]).toBeUndefined();
    expect(typeof result["strategy"]).toBe("string");
    expect((result["strategy"] as string).length).toBeGreaterThan(0);
  });

  it("still routes a base A2A skill (list_scorers)", async () => {
    const result = (await dispatchAnySkill("list_scorers", {})) as Record<
      string,
      unknown
    >;
    expect(Array.isArray(result["scorers"])).toBe(true);
    expect((result["scorers"] as unknown[]).length).toBeGreaterThan(0);
  });

  it("routes a base scoring skill (score)", async () => {
    const result = (await dispatchAnySkill("score", {
      a: "John",
      b: "Jon",
      scorer: "jaro_winkler",
    })) as Record<string, unknown>;
    expect(typeof result["score"]).toBe("number");
  });

  it("throws on an unknown skill id", async () => {
    await expect(
      dispatchAnySkill("not_a_real_skill", {}),
    ).rejects.toThrow(/Unknown skill/);
  });
});
