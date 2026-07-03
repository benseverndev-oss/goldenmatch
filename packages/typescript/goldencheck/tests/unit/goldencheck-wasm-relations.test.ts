/**
 * Reroute equivalence for the FD-discovery + near-duplicate relations: with the
 * wasm backend enabled they run the shared goldencheck-core kernels; disabled,
 * the pure-TS implementations. Both must produce identical findings, which is
 * what makes the Rust core the source of truth (pure-TS = faithful fallback).
 */
import { describe, it, expect, afterEach } from "vitest";
import { TabularData } from "../../src/core/data.js";
import { FunctionalDependencyProfiler } from "../../src/core/relations/functional-dependency.js";
import { FuzzyValuesProfiler } from "../../src/core/profilers/fuzzy-values.js";
import {
  enableGoldencheckWasm,
  disableGoldencheckWasm,
} from "../../src/core/goldencheckWasm.js";
import type { Finding } from "../../src/core/types.js";

// A stable, comparable projection of the salient finding fields.
function project(findings: Finding[]): string[] {
  return findings
    .map((f) =>
      JSON.stringify({
        check: f.check,
        column: f.column,
        severity: f.severity,
        meta: f.metadata,
      }),
    )
    .sort();
}

function bothPaths(run: () => Finding[]) {
  disableGoldencheckWasm();
  const pureTs = project(run());
  enableGoldencheckWasm();
  const wasm = project(run());
  return { pureTs, wasm };
}

describe("goldencheck wasm reroute — FD discovery + near-duplicate equivalence", () => {
  afterEach(() => disableGoldencheckWasm());

  it("FunctionalDependencyProfiler: wasm == pure-TS", () => {
    const prof = new FunctionalDependencyProfiler();
    // 60 rows: dept -> dept_name is a strict FD; region correlates but with a
    // violation so it is NOT an FD; a free-form col determines nothing.
    const depts = ["A", "B", "C", "D"];
    const names: Record<string, string> = { A: "Sales", B: "Eng", C: "Ops", D: "HR" };
    const rows = Array.from({ length: 60 }, (_, i) => {
      const d = depts[i % depts.length]!;
      return {
        dept: d,
        dept_name: names[d]!,
        region: i % 7 === 0 ? "X" : d === "A" ? "N" : "S", // occasional violation
        note: `n${i % 5}`,
      };
    });
    const data = new TabularData(rows);
    const { pureTs, wasm } = bothPaths(() => prof.profile(data));
    expect(wasm).toEqual(pureTs);
    expect(pureTs.length).toBeGreaterThan(0); // the FD is actually found
  });

  it("FuzzyValuesProfiler: wasm == pure-TS", () => {
    const prof = new FuzzyValuesProfiler();
    // 60 rows over a column with typo-close encodings of a few states.
    const variants = [
      "California",
      "Californa",
      "CALIFORNIA ",
      "Texas",
      "Texbs",
      "Oregon",
      "Oregonn",
      "Nevada",
    ];
    const rows = Array.from({ length: 60 }, (_, i) => ({
      state: variants[i % variants.length]!,
      other: `v${i % 3}`,
    }));
    const data = new TabularData(rows);
    const { pureTs, wasm } = bothPaths(() => prof.profile(data, "state"));
    expect(wasm).toEqual(pureTs);
    expect(pureTs.length).toBeGreaterThan(0); // near-dup clusters are actually found
  });
});
