/**
 * mcp-review-config.test.ts -- Task 11: the healer's `review_config` on the MCP
 * surface.
 *
 * `review_config` is registered once in `AGENT_SKILLS`, so it auto-renders as an
 * MCP tool (via AGENT_MCP_TOOLS -> TOOLS) and dispatches through
 * `handleAgentTool` -> `dispatchSkill` -> the skill handler, which calls the
 * core `reviewConfig`. The dynamic `tool_count` (server_info === TOOLS.length)
 * still holds. Graceful-empty: a null backend returns `{ suggestions: [] }`
 * (never throws).
 */
import { describe, it, expect, afterEach } from "vitest";
import { TOOLS, handleTool } from "../../src/node/mcp/server.js";
import { makeConfig } from "../../src/core/types.js";
import {
  setSuggestWasmBackend,
  disableSuggestWasm,
  type SuggestWasmBackend,
} from "../../src/core/suggestWasmBackend.js";

const config = makeConfig({
  matchkeys: [
    {
      name: "person",
      type: "weighted",
      fields: [
        { field: "name", transforms: [], scorer: "jaro_winkler", weight: 1.0 },
      ],
      threshold: 0.5,
    },
  ],
});

const rows = [{ name: "alice" }, { name: "alicia" }, { name: "bob" }];

afterEach(() => disableSuggestWasm());

describe("MCP review_config tool", () => {
  it("is listed in TOOLS", () => {
    const names = new Set(TOOLS.map((t) => t.name));
    expect(names.has("review_config")).toBe(true);
  });

  it("keeps the dynamic tool_count in sync (server_info === TOOLS.length)", async () => {
    const info = (await handleTool("server_info", {})) as { tool_count: number };
    expect(info.tool_count).toBe(TOOLS.length);
  });

  it("dispatches to reviewConfig and is graceful-empty with no backend", async () => {
    const result = (await handleTool("review_config", { rows, config })) as {
      suggestions: unknown[];
      count: number;
      error?: string;
    };
    expect(result.error).toBeUndefined();
    expect(Array.isArray(result.suggestions)).toBe(true);
    expect(result.suggestions).toEqual([]);
    expect(result.count).toBe(0);
  });

  it("returns serialized suggestions (verified:true) when a backend is registered", async () => {
    // Patch references a non-existent matchkey, so the verify pass's applyPatch
    // throws and the suggestion is conservatively KEPT -> deterministic
    // verified:true regardless of cluster-health movement.
    const kernelJson = JSON.stringify([
      {
        id: "thr:raise:person",
        kind: "raise_threshold",
        target: "person",
        rationale: "raise it",
        confidence: 0.7,
        patch: { op: "set_threshold", matchkey: "__no_such_mk__", value: 0.92 },
      },
    ]);
    const stub: SuggestWasmBackend = { suggestReview: () => kernelJson };
    setSuggestWasmBackend(stub);

    const result = (await handleTool("review_config", { rows, config })) as {
      suggestions: { id: string; kind: string; verified: boolean }[];
      count: number;
    };
    expect(result.count).toBeGreaterThanOrEqual(1);
    expect(result.suggestions[0]!.kind).toBe("raise_threshold");
    expect(result.suggestions[0]!.verified).toBe(true);
  });
});
