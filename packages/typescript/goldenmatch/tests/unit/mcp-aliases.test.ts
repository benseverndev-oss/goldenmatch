import { describe, it, expect } from "vitest";
import { TOOLS, handleTool } from "../../src/node/mcp/server.js";

const ALIAS_TO_CANONICAL: Record<string, string> = {
  find_duplicates: "dedupe",
  match_record: "match",
  explain_match: "explain_pair",
  profile_data: "profile",
};

describe("MCP naming aliases", () => {
  it("advertises all four alias names", () => {
    const names = new Set(TOOLS.map((t) => t.name));
    for (const alias of Object.keys(ALIAS_TO_CANONICAL)) {
      expect(names.has(alias)).toBe(true);
    }
  });

  it("each alias schema equals its canonical schema", () => {
    const byName = new Map(TOOLS.map((t) => [t.name, t]));
    for (const [alias, canonical] of Object.entries(ALIAS_TO_CANONICAL)) {
      expect(byName.get(alias)!.inputSchema).toEqual(byName.get(canonical)!.inputSchema);
      expect(byName.get(alias)!.description).toContain(canonical);
    }
  });

  it("profile_data dispatches identically to profile", async () => {
    const viaAlias = await handleTool("profile_data", { path: "nonexistent_xyz.csv" });
    const viaCanonical = await handleTool("profile", { path: "nonexistent_xyz.csv" });
    expect(viaAlias).toEqual(viaCanonical);
  });
});
