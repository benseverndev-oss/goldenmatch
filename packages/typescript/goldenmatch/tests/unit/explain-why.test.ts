/**
 * explain-why.test.ts -- whyForCorrection + ReviewItem.why integration.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { whyForCorrection } from "../../src/core/llm/explain.js";
import type { Row, MatchkeyField } from "../../src/core/types.js";
import { makeMatchkeyField } from "../../src/core/types.js";
import type { ReviewItem } from "../../src/core/review-queue.js";

const fields: MatchkeyField[] = [
  makeMatchkeyField({
    field: "name",
    transforms: ["lowercase", "strip"],
    scorer: "jaro_winkler",
    weight: 1,
  }),
  makeMatchkeyField({
    field: "zip",
    transforms: ["lowercase", "strip"],
    scorer: "exact",
    weight: 1,
  }),
];

const rows: Row[] = [
  { __row_id__: 1, name: "John Smith", zip: "01234" },
  { __row_id__: 2, name: "Jon Smith", zip: "01234" },
];

describe("ReviewItem.why field", () => {
  it("ReviewItem type accepts an optional why string", () => {
    const item: ReviewItem = {
      pairId: "1:2",
      idA: 1,
      idB: 2,
      score: 0.9,
      status: "pending",
      createdAt: Date.now(),
      why: "matched on name with score 0.92",
    };
    expect(item.why).toBe("matched on name with score 0.92");
  });
});

describe("whyForCorrection (deterministic default)", () => {
  it("returns a non-empty deterministic phrase mentioning the matchkey fields", async () => {
    const why = await whyForCorrection(
      { idA: 1, idB: 2, originalScore: 0.92 },
      rows,
      fields,
    );
    expect(why.length).toBeGreaterThan(0);
    expect(why).toMatch(/name/);
    expect(why).toMatch(/zip/);
    expect(why).toMatch(/0\.92/);
  });

  it("works without rows or matchkey fields (graceful fallback)", async () => {
    const why = await whyForCorrection({ idA: 5, idB: 6, score: 0.7 }, [], []);
    expect(why).toMatch(/\(5, 6\)/);
    expect(why).toMatch(/0\.70/);
  });

  it("does not call LLM when useLlm is false", async () => {
    // No API key in test env (or one is irrelevant); useLlm defaults to false.
    const why = await whyForCorrection(
      { idA: 1, idB: 2, originalScore: 0.92 },
      rows,
      fields,
      { useLlm: false },
    );
    expect(why).toMatch(/score 0\.92/);
  });
});

describe("whyForCorrection LLM upgrade path", () => {
  const origOpenai = process.env.OPENAI_API_KEY;
  const origAnthropic = process.env.ANTHROPIC_API_KEY;

  beforeEach(() => {
    delete process.env.OPENAI_API_KEY;
    delete process.env.ANTHROPIC_API_KEY;
  });

  afterEach(() => {
    if (origOpenai !== undefined) process.env.OPENAI_API_KEY = origOpenai;
    if (origAnthropic !== undefined)
      process.env.ANTHROPIC_API_KEY = origAnthropic;
  });

  it("falls back to deterministic when no API key is set even with useLlm=true", async () => {
    const why = await whyForCorrection(
      { idA: 1, idB: 2, originalScore: 0.92 },
      rows,
      fields,
      { useLlm: true },
    );
    expect(why).toMatch(/score 0\.92/);
  });

  it("falls back to deterministic when LLM client throws (key set, no SDK installed)", async () => {
    // The `openai` peer dep is not installed in this repo; the dynamic import
    // throws, our code catches and falls back. Verifies the catch path.
    process.env.OPENAI_API_KEY = "sk-test-fake";
    const why = await whyForCorrection(
      { idA: 1, idB: 2, originalScore: 0.92 },
      rows,
      fields,
      { useLlm: true },
    );
    expect(why).toMatch(/score 0\.92/);
  });
});
