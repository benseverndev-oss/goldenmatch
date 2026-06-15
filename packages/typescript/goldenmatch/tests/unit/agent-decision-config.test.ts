import { describe, it, expect } from "vitest";
import {
  buildAlternatives,
  decisionToConfig,
} from "../../src/core/agent/strategy.js";
import type { StrategyDecision } from "../../src/core/agent/types.js";

const decision = (o: Partial<StrategyDecision>): StrategyDecision => ({
  strategy: "fuzzy",
  why: "",
  domain: null,
  strong_ids: [],
  fuzzy_fields: [],
  backend: null,
  auto_execute: true,
  ...o,
});

describe("buildAlternatives", () => {
  it("offers pprl + fellegi_sunter for a fuzzy decision", () => {
    const alts = buildAlternatives(decision({ strategy: "fuzzy" }));
    expect(alts.map((a) => a.strategy)).toEqual(["pprl", "fellegi_sunter"]);
  });

  it("omits pprl from the alternatives when the decision is already pprl", () => {
    const alts = buildAlternatives(decision({ strategy: "pprl" }));
    expect(alts.map((a) => a.strategy)).toEqual(["fellegi_sunter"]);
  });
});

describe("decisionToConfig", () => {
  it("builds exact + weighted matchkeys + blocking", () => {
    const cfg = decisionToConfig(
      decision({
        strategy: "exact_then_fuzzy",
        strong_ids: ["id"],
        fuzzy_fields: ["name"],
      }),
    );
    const names = (cfg.matchkeys ?? []).map((m) => m.name);
    expect(names).toContain("exact_id");
    expect(names).toContain("fuzzy");

    const exact = (cfg.matchkeys ?? []).find((m) => m.name === "exact_id")!;
    expect(exact.type).toBe("exact");
    expect(exact.fields[0]!.field).toBe("id");
    expect(exact.fields[0]!.scorer).toBe("exact");

    const fuzzy = (cfg.matchkeys ?? []).find((m) => m.name === "fuzzy")!;
    expect(fuzzy.type).toBe("weighted");
    expect(fuzzy.fields[0]!.scorer).toBe("jaro_winkler");
    expect(fuzzy.fields[0]!.weight).toBe(1.0);

    expect(cfg.blocking).toBeDefined();
    expect(cfg.blocking!.strategy).toBe("static");
    expect(cfg.blocking!.keys[0]!.fields).toEqual(["name"]);
    expect(cfg.blocking!.keys[0]!.transforms).toEqual([
      "lowercase",
      "first_token",
    ]);
  });

  it("builds no blocking when there are no fuzzy fields", () => {
    const cfg = decisionToConfig(
      decision({ strategy: "exact_only", strong_ids: ["id"] }),
    );
    expect(cfg.blocking).toBeUndefined();
    expect((cfg.matchkeys ?? []).map((m) => m.name)).toEqual(["exact_id"]);
  });

  it("propagates backend onto the config", () => {
    const cfg = decisionToConfig(
      decision({ strategy: "fuzzy", fuzzy_fields: ["name"], backend: "ray" }),
    );
    expect(cfg.backend).toBe("ray");
  });
});
