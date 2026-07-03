/**
 * Cross-surface parity: the TS/WASM surface must reproduce the SAME golden
 * outputs as the Rust core (`goldencheck-core/tests/golden.rs`) and the Python
 * `goldencheck-native` wheel. The fixture is generated once from the kernel and
 * copied here by `scripts/build_goldencheck_wasm.mjs`; all surfaces run the SAME
 * `goldencheck-core` code, so the outputs match exactly.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import {
  discoverFunctionalDependencies,
  discoverApproximateFds,
  functionalDependencyHolds,
  fdViolationRows,
  compositeKeySearch,
  benfordLeadingDigits,
  nearDuplicateClusters,
} from "../../src/core/goldencheckWasm.js";

const here = dirname(fileURLToPath(import.meta.url));
const fx = JSON.parse(readFileSync(resolve(here, "fixtures/gc_vectors.json"), "utf8"));

describe("goldencheck wasm — cross-surface parity", () => {
  it("discover_functional_dependencies", () => {
    const got = discoverFunctionalDependencies(fx.fd.columns);
    const want = fx.fd.discover_expected.map((p: number[]) => [p[0], p[1]]);
    expect(got).toEqual(want);
  });

  it("discover_approximate_fds", () => {
    expect(discoverApproximateFds(fx.fd.columns, fx.fd.approx_min_confidence)).toEqual(
      fx.fd.approx_expected,
    );
  });

  it("functional_dependency_holds + fd_violation_rows", () => {
    const det = fx.fd.columns[fx.fd.holds_det];
    const dep = fx.fd.columns[fx.fd.holds_dep];
    expect(functionalDependencyHolds(det, dep)).toBe(fx.fd.holds_expected);
    expect(fdViolationRows(det, dep)).toEqual(fx.fd.violation_rows_expected);
  });

  it("composite_key_search", () => {
    const ck = fx.composite_key;
    expect(compositeKeySearch(ck.columns, ck.max_size, ck.single_unique)).toEqual(ck.expected);
  });

  it("benford_leading_digits", () => {
    expect(benfordLeadingDigits(fx.benford.values)).toEqual(fx.benford.expected);
  });

  it("near_duplicate_clusters", () => {
    expect(nearDuplicateClusters(fx.near_dup.values, fx.near_dup.min_similarity)).toEqual(
      fx.near_dup.expected,
    );
  });
});
