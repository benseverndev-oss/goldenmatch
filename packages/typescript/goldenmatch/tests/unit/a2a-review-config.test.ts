/**
 * a2a-review-config.test.ts -- Task 11: the healer's `review_config` on the A2A
 * surface.
 *
 * Added to `AGENT_SKILLS` only (NOT the hardcoded BASE_SKILLS), so
 * `buildCardSkills` auto-unions it onto the agent card and `dispatchAnySkill`
 * routes it (AGENT_TOOL_NAMES -> handleAgentTool -> dispatchSkill -> the
 * handler that calls core `reviewConfig`). Graceful-empty when no backend.
 */
import { describe, it, expect, afterEach } from "vitest";
import { AGENT_CARD, dispatchAnySkill } from "../../src/node/a2a/server.js";
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

describe("A2A review_config skill", () => {
  it("is advertised on the agent card", () => {
    const ids = new Set(AGENT_CARD.skills.map((s) => s.id));
    expect(ids.has("review_config")).toBe(true);
  });

  it("appears exactly once (de-duped by id)", () => {
    const count = AGENT_CARD.skills.filter((s) => s.id === "review_config").length;
    expect(count).toBe(1);
  });

  it("dispatches and is graceful-empty with no backend", async () => {
    const result = (await dispatchAnySkill("review_config", { rows, config })) as {
      suggestions: unknown[];
      count: number;
      error?: string;
    };
    expect(result.error).toBeUndefined();
    expect(result.suggestions).toEqual([]);
    expect(result.count).toBe(0);
  });

  it("returns serialized suggestions when a backend is registered", async () => {
    // Non-existent matchkey => verify's applyPatch throws => conservatively
    // kept => deterministic verified:true.
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

    const result = (await dispatchAnySkill("review_config", { rows, config })) as {
      suggestions: { kind: string; verified: boolean }[];
      count: number;
    };
    expect(result.count).toBeGreaterThanOrEqual(1);
    expect(result.suggestions[0]!.kind).toBe("raise_threshold");
    expect(result.suggestions[0]!.verified).toBe(true);
  });
});
