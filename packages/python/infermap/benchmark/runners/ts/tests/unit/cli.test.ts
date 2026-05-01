import { describe, it, expect } from "vitest";
import { resolve } from "node:path";
import { existsSync } from "node:fs";
import { matchesFilter, REPO_ROOT } from "../../src/cli.js";
import type { CaseRef } from "../../src/types.js";

function makeRef(
  id: string,
  category: "valentine" | "real_world" | "synthetic" = "valentine",
  difficulty: "easy" | "medium" | "hard" = "easy",
  tags: string[] = [],
): CaseRef {
  return {
    id,
    path: "cases/test",
    category,
    subcategory: "test",
    source: { name: "x", url: "x", license: "MIT", attribution: "x" },
    tags,
    expectedDifficulty: difficulty,
    fieldCounts: { source: 1, target: 1 },
  };
}

describe("matchesFilter helper", () => {
  it("filters by category", () => {
    expect(matchesFilter(makeRef("a", "valentine"), "category:valentine")).toBe(true);
    expect(matchesFilter(makeRef("a", "valentine"), "category:synthetic")).toBe(false);
  });

  it("filters by difficulty", () => {
    expect(matchesFilter(makeRef("a", "valentine", "hard"), "difficulty:hard")).toBe(true);
    expect(matchesFilter(makeRef("a", "valentine", "easy"), "difficulty:hard")).toBe(false);
  });

  it("filters by tag", () => {
    expect(
      matchesFilter(makeRef("a", "valentine", "easy", ["alias_dominant"]), "tag:alias_dominant"),
    ).toBe(true);
    expect(
      matchesFilter(makeRef("a", "valentine", "easy", ["other"]), "tag:alias_dominant"),
    ).toBe(false);
  });

  it("filters by id prefix when no colon", () => {
    expect(matchesFilter(makeRef("valentine/magellan/foo"), "valentine/")).toBe(true);
    expect(matchesFilter(makeRef("synthetic/customer/easy/0"), "valentine/")).toBe(false);
  });

  it("unknown filter key returns false", () => {
    expect(matchesFilter(makeRef("a"), "nosuchkey:x")).toBe(false);
  });
});

describe("REPO_ROOT resolution", () => {
  it("points at the repo root", () => {
    expect(existsSync(resolve(REPO_ROOT, "pyproject.toml"))).toBe(true);
    expect(existsSync(resolve(REPO_ROOT, "benchmark"))).toBe(true);
    expect(existsSync(resolve(REPO_ROOT, "packages/infermap-js"))).toBe(true);
  });
});
