/**
 * Unit tests for the match adapter's `buildConfigFromContexts` heuristics
 * (cardinality fallback floor + geo-compound blocking selection).
 */

import { describe, it, expect } from "vitest";
import {
  buildConfigFromContexts,
  makeColumnContext,
  ColumnType,
  type ColumnContext,
  type Row,
} from "../../src/core/index.js";

function nameCtx(name: string): ColumnContext {
  return makeColumnContext({ name, inferredType: ColumnType.NAME, isIdentifier: true });
}

describe("buildConfigFromContexts", () => {
  it("builds exact email + fuzzy name matchkeys", () => {
    const contexts = [
      nameCtx("first_name"),
      nameCtx("last_name"),
      makeColumnContext({ name: "email", inferredType: ColumnType.EMAIL }),
    ];
    const rows: Row[] = [{ first_name: "a", last_name: "b", email: "a@b.com" }];
    const cfg = buildConfigFromContexts(contexts, rows)!;
    expect(cfg).not.toBeNull();
    const names = cfg.matchkeys!.map((m) => m.name);
    expect(names).toContain("exact_email");
    expect(names).toContain("fuzzy_names");
  });

  it("uses last_name + geo compound blocking when present", () => {
    const contexts = [
      nameCtx("first_name"),
      nameCtx("last_name"),
      makeColumnContext({ name: "state", inferredType: ColumnType.GEO }),
      makeColumnContext({ name: "city", inferredType: ColumnType.GEO }),
    ];
    // state has lower cardinality than city -> preferred as the geo anchor.
    const rows: Row[] = [
      { first_name: "a", last_name: "x", state: "MA", city: "Boston" },
      { first_name: "b", last_name: "y", state: "MA", city: "Cambridge" },
      { first_name: "c", last_name: "z", state: "CA", city: "Oakland" },
    ];
    const cfg = buildConfigFromContexts(contexts, rows)!;
    expect(cfg.blocking!.strategy).toBe("multi_pass");
    // First pass key combines the chosen geo (state) and the last_name.
    const firstPass = cfg.blocking!.keys[0]!;
    expect(firstPass.fields).toEqual(["state", "last_name"]);
  });

  it("returns null when only low-cardinality string columns exist", () => {
    // No name/email signal, and cardinality below the 5%/floor cutoff.
    const contexts = [
      makeColumnContext({ name: "status", inferredType: ColumnType.STRING }),
    ];
    const rows: Row[] = Array.from({ length: 100 }, () => ({ status: "active" }));
    expect(buildConfigFromContexts(contexts, rows)).toBeNull();
  });

  it("builds a string fallback matchkey for high-cardinality strings", () => {
    const contexts = [makeColumnContext({ name: "label", inferredType: ColumnType.STRING })];
    const rows: Row[] = Array.from({ length: 100 }, (_, i) => ({ label: `v${i}` }));
    const cfg = buildConfigFromContexts(contexts, rows)!;
    expect(cfg.matchkeys!.map((m) => m.name)).toContain("fuzzy_fallback");
  });
});
