import { describe, it, expect } from "vitest";
import { LLMScorer } from "../../src/core/scorers/llm.js";
import { makeFieldInfo } from "../../src/core/types.js";

describe("LLMScorer", () => {
  it("abstains by default", () => {
    const scorer = new LLMScorer();
    const r = scorer.score(
      makeFieldInfo({ name: "a" }),
      makeFieldInfo({ name: "b" })
    );
    expect(r).toBeNull();
  });

  it("defaults weight to 0.8", () => {
    expect(new LLMScorer().weight).toBe(0.8);
    expect(new LLMScorer({ weight: 0.5 }).weight).toBe(0.5);
  });

  it("stores adapter for future async path", () => {
    const adapter = async () => "yes";
    const scorer = new LLMScorer({ adapter });
    expect(scorer.adapter).toBe(adapter);
  });
});
