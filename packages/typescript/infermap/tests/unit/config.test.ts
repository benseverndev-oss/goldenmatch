import { describe, it, expect } from "vitest";
import {
  loadEngineConfig,
  applyScorerOverrides,
  fromConfig,
  mapResultToConfigJson,
  ConfigError,
} from "../../src/core/config.js";
import { defaultScorers } from "../../src/core/scorers/registry.js";
import type { MapResult } from "../../src/core/types.js";

describe("loadEngineConfig", () => {
  it("parses scorer overrides and aliases", () => {
    const cfg = loadEngineConfig(
      JSON.stringify({
        scorers: {
          ExactScorer: { weight: 0.5 },
          LLMScorer: { enabled: false },
        },
        aliases: {
          order_id: ["ord_no", "order_number"],
        },
      })
    );
    expect(cfg.scorers?.["ExactScorer"]).toEqual({ weight: 0.5 });
    expect(cfg.scorers?.["LLMScorer"]).toEqual({ enabled: false });
    expect(cfg.aliases?.["order_id"]).toEqual(["ord_no", "order_number"]);
  });

  it("accepts a pre-parsed object", () => {
    const cfg = loadEngineConfig({ scorers: {} });
    expect(cfg.scorers).toEqual({});
  });

  it("throws on non-object root", () => {
    expect(() => loadEngineConfig("[]")).toThrow(ConfigError);
  });

  it("throws on non-array alias list", () => {
    expect(() =>
      loadEngineConfig(JSON.stringify({ aliases: { foo: "nope" } }))
    ).toThrow(ConfigError);
  });
});

describe("applyScorerOverrides", () => {
  it("drops disabled scorers", () => {
    const base = defaultScorers();
    const out = applyScorerOverrides(base, { ExactScorer: { enabled: false } });
    expect(out.find((s) => s.name === "ExactScorer")).toBeUndefined();
    expect(out.length).toBe(base.length - 1);
  });

  it("reweights without mutating originals", () => {
    const base = defaultScorers();
    const originalWeight = base[0]!.weight;
    const out = applyScorerOverrides(base, {
      [base[0]!.name]: { weight: 0.123 },
    });
    expect(out[0]!.weight).toBe(0.123);
    expect(base[0]!.weight).toBe(originalWeight);
  });

  it("passes through when no overrides given", () => {
    const base = defaultScorers();
    const out = applyScorerOverrides(base, undefined);
    expect(out).toHaveLength(base.length);
  });
});

describe("fromConfig / mapResultToConfigJson round-trip", () => {
  const original: MapResult = {
    mappings: [
      {
        source: "a",
        target: "b",
        confidence: 0.987654,
        breakdown: {},
        reasoning: "not serialized",
      },
      {
        source: "c",
        target: "d",
        confidence: 0.5,
        breakdown: {},
        reasoning: "",
      },
    ],
    unmappedSource: ["x"],
    unmappedTarget: ["y", "z"],
    warnings: [],
    metadata: {},
  };

  it("writes and reads back identical mapping pairs", () => {
    const json = mapResultToConfigJson(original);
    const parsed = JSON.parse(json);
    expect(parsed.version).toBe("1");
    expect(parsed.mappings[0].confidence).toBe(0.988); // rounded to 3 dp

    const restored = fromConfig(json);
    expect(restored.mappings).toHaveLength(2);
    expect(restored.mappings[0]!.source).toBe("a");
    expect(restored.mappings[0]!.target).toBe("b");
    expect(restored.mappings[0]!.confidence).toBe(0.988);
    expect(restored.unmappedSource).toEqual(["x"]);
    expect(restored.unmappedTarget).toEqual(["y", "z"]);
  });

  it("throws on missing 'mappings' key", () => {
    expect(() => fromConfig('{"something":"else"}')).toThrow(ConfigError);
  });

  it("throws on non-array mappings", () => {
    expect(() => fromConfig('{"mappings":"nope"}')).toThrow(ConfigError);
  });
});
