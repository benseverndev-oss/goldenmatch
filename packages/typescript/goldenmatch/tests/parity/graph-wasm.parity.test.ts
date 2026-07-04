/**
 * Cross-surface parity: the graph (connected-components) wasm kernel reproduces
 * the SAME partition as the Rust `graph-core` (`tests/golden.rs`) and the
 * Python-native / DuckDB / Postgres surfaces — the shared `graph_golden.json`
 * oracle. The kernel returns components in HashMap order; the partition is
 * unique, so we canonicalize (members ascending, groups by min) before
 * comparing, exactly as every caller does.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { connectedComponents } from "../../src/core/graphWasm.js";

interface GoldenCase {
  name: string;
  edges: [number, number][];
  all_ids: number[];
  components: number[][];
}

const here = dirname(fileURLToPath(import.meta.url));
const cases: GoldenCase[] = JSON.parse(
  readFileSync(resolve(here, "fixtures/graph/graph_golden.json"), "utf8"),
);

function canonical(comps: number[][]): number[][] {
  return comps
    .map((c) => [...c].sort((a, b) => a - b))
    .sort((a, b) => a[0]! - b[0]!);
}

describe("graph-wasm parity — reproduces the shared golden fixture", () => {
  it("has edge coverage", () => {
    expect(cases.length).toBeGreaterThanOrEqual(5);
  });

  for (const c of cases) {
    it(`connected components: ${c.name}`, () => {
      const got = connectedComponents(c.edges, c.all_ids);
      expect(canonical(got)).toEqual(canonical(c.components));
    });
  }
});
