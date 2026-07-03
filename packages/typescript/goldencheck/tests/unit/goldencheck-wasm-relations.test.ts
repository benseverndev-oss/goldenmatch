/**
 * Reroute equivalence for the FD-discovery + near-duplicate relations: with the
 * wasm backend enabled they run the shared goldencheck-core kernels; disabled,
 * the pure-TS implementations. Both must produce identical findings, which is
 * what makes the Rust core the source of truth (pure-TS = faithful fallback).
 */
import { describe, it, expect, afterEach } from "vitest";
import { TabularData } from "../../src/core/data.js";
import { FunctionalDependencyProfiler } from "../../src/core/relations/functional-dependency.js";
import { ApproximateFDProfiler } from "../../src/core/relations/approx-fd.js";
import { FuzzyValuesProfiler } from "../../src/core/profilers/fuzzy-values.js";
import { profileStatistical } from "../../src/core/baseline/statistical.js";
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

  it("ApproximateFDProfiler: wasm == pure-TS", () => {
    const prof = new ApproximateFDProfiler();
    // 200 rows: zip -> city is a near-strict FD with a handful of injected
    // violations (a real approximate FD). dept -> dept_name is strict (viol=0,
    // excluded). A free-form note determines nothing. This exercises BOTH the
    // discover_approximate_fds and fd_violation_rows kernels.
    const zips = ["10001", "20002", "30003", "40004", "50005"];
    const cityOf: Record<string, string> = {
      "10001": "NYC", "20002": "DC", "30003": "ATL", "40004": "MIA", "50005": "SEA",
    };
    const rows = Array.from({ length: 200 }, (_, i) => {
      const zip = zips[i % zips.length]!;
      // Inject ~5 violations: a few rows get the "wrong" city for their zip.
      const city = i % 41 === 7 ? "TYPO" : cityOf[zip]!;
      return {
        zip,
        city,
        dept: `d${i % 4}`,
        dept_name: `name${i % 4}`, // strict FD dept -> dept_name (viol 0)
        note: `free${i}`,
      };
    });
    const data = new TabularData(rows);
    const { pureTs, wasm } = bothPaths(() => prof.profile(data));
    expect(wasm).toEqual(pureTs);
    expect(pureTs.length).toBeGreaterThan(0); // an approximate FD is actually found
  });

  it("Benford (profileStatistical): wasm == pure-TS", () => {
    // A Benford-eligible column ('amount') spanning >= 2 orders of magnitude, so
    // maybeBenford runs computeBenford — the histogram step now reroutes to the
    // shared kernel when wasm is on.
    const rows = Array.from({ length: 120 }, (_, i) => {
      // A spread of magnitudes 1..~50000 with varied leading digits.
      const amount = ((i * 37 + 1) % 900) * 10 ** (i % 4) + (i % 9) + 1;
      return { amount, note: `n${i % 3}` };
    });
    const data = new TabularData(rows);

    disableGoldencheckWasm();
    const pureTs = JSON.stringify(profileStatistical(data).amount!.benford);
    enableGoldencheckWasm();
    const wasm = JSON.stringify(profileStatistical(data).amount!.benford);

    expect(wasm).toEqual(pureTs);
    // The Benford check actually ran (non-null result), so the reroute is exercised.
    expect(profileStatistical(data).amount!.benford).not.toBeNull();
  });
});
