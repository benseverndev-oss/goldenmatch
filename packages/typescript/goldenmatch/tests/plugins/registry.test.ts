/**
 * Tests for PluginRegistry singleton (Phase 5 Part 5/N -- #208).
 */
import { beforeEach, describe, expect, it } from "vitest";

import type { GoldenStrategyPlugin } from "../../src/core/plugins/base.js";
import {
  BUILTIN_PLUGINS,
  PluginRegistry,
} from "../../src/core/plugins/registry.js";

describe("PluginRegistry", () => {
  beforeEach(() => {
    PluginRegistry.reset();
  });

  it("instance() returns the same singleton across calls", () => {
    const a = PluginRegistry.instance();
    const b = PluginRegistry.instance();
    expect(a).toBe(b);
  });

  it("reset() clears the singleton", () => {
    const a = PluginRegistry.instance();
    PluginRegistry.reset();
    const b = PluginRegistry.instance();
    expect(a).not.toBe(b);
  });

  it("discover() registers all 22 builtin plugins", () => {
    const r = PluginRegistry.instance();
    r.discover();
    const names = r.listPlugins().golden_strategy;
    expect(names.length).toBe(22);
    expect(BUILTIN_PLUGINS.length).toBe(22);
  });

  it("discover() is idempotent", () => {
    const r = PluginRegistry.instance();
    r.discover();
    r.discover();
    r.discover();
    expect(r.listPlugins().golden_strategy.length).toBe(22);
  });

  it.each([
    ["numeric_max"],
    ["numeric_min"],
    ["shortest_value"],
    ["email_normalize"],
    ["url_canonical"],
    ["system_of_record"],
    ["lifecycle_stage"],
    ["freshness_with_max_age"],
    ["weighted_by_recency"],
    ["count_distinct"],
    ["agreement_rate"],
  ])("discover() makes builtin %s resolvable", (name) => {
    const r = PluginRegistry.instance();
    r.discover();
    expect(r.hasGoldenStrategy(name)).toBe(true);
    const plugin = r.getGoldenStrategy(name);
    expect(plugin).not.toBeNull();
    expect(plugin!.name).toBe(name);
  });

  it("getGoldenStrategy returns null for unknown name", () => {
    const r = PluginRegistry.instance();
    r.discover();
    expect(r.getGoldenStrategy("nonexistent_strategy")).toBeNull();
    expect(r.hasGoldenStrategy("nonexistent_strategy")).toBe(false);
  });

  it("user-registered plugin overrides builtin (last-write wins)", () => {
    const r = PluginRegistry.instance();
    r.discover();

    const customMax: GoldenStrategyPlugin = {
      name: "numeric_max",
      merge: () => ["custom" as unknown, 1.0, 0] as const,
    };
    r.registerGoldenStrategy("numeric_max", customMax);

    const resolved = r.getGoldenStrategy("numeric_max");
    expect(resolved).toBe(customMax);
    expect(r.listPlugins().golden_strategy.length).toBe(22);
  });

  it("registerGoldenStrategy rejects plugin without merge method", () => {
    const r = PluginRegistry.instance();
    const broken = { name: "bad" } as unknown as GoldenStrategyPlugin;
    expect(() => r.registerGoldenStrategy("bad", broken)).toThrow(/merge/);
  });

  it("registerGoldenStrategy adds a NEW plugin not previously seen", () => {
    const r = PluginRegistry.instance();
    r.discover();
    const custom: GoldenStrategyPlugin = {
      name: "my_custom",
      merge: (values) => [values[0], 1.0, 0] as const,
    };
    r.registerGoldenStrategy("my_custom", custom);
    expect(r.hasGoldenStrategy("my_custom")).toBe(true);
    expect(r.listPlugins().golden_strategy.length).toBe(23);
  });

  it("listPlugins shape mirrors Python sibling (4 keys)", () => {
    const r = PluginRegistry.instance();
    r.discover();
    const all = r.listPlugins();
    expect(Object.keys(all).sort()).toEqual(
      ["connector", "golden_strategy", "scorer", "transform"],
    );
    // TS port v2.0 only ships golden_strategy; the others are stubs.
    expect(all.scorer).toEqual([]);
    expect(all.transform).toEqual([]);
    expect(all.connector).toEqual([]);
  });
});
