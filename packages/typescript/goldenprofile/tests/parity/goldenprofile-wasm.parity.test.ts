/**
 * Cross-surface parity: the wasm kernel must reproduce the host oracle
 * (`goldenprofile-core::resolve_json`, the SAME kernel the Python `-native`
 * wheel wraps) — the same cluster PARTITION and the same edge set + scores (4dp).
 *
 * NOTE: the kernel emits cluster/edge ORDERING that is not canonical across
 * builds (it falls out of hash-map iteration order, build-dependent), so the
 * cross-surface invariant is the partition (set of sets) and the edge set, NOT
 * the byte ordering. Both sides are canonicalized before comparison: members
 * sorted within each cluster, clusters sorted, edges put a<=b and sorted.
 *
 * The fixtures in `fixtures/goldenprofile/resolutions.json` are authored from
 * the host boundary by `goldenprofile-wasm/examples/gen_parity_fixtures.rs`;
 * the Python side asserts the same file in
 * `packages/python/goldengraph/tests/test_goldenprofile_wasm_crossparity.py`,
 * closing the Python <-> WASM loop through one source of truth.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, it, expect, beforeAll } from "vitest";
import { resolveProfiles, type Resolution, type ResolveRequest } from "../../src/index.js";
import { enableGoldenprofileWasm } from "../../src/core/goldenprofileWasm.js";

interface Case {
  name: string;
  request: ResolveRequest;
  expected: Resolution;
}

const here = dirname(fileURLToPath(import.meta.url));
const fixtures: { cases: Case[] } = JSON.parse(
  readFileSync(resolve(here, "fixtures/goldenprofile/resolutions.json"), "utf8"),
);

/** Round every float in an edge score to 4dp so formatting can't cause noise. */
function round4(n: number): number {
  return Math.round(n * 1e4) / 1e4;
}

/** Canonical partition: sort members within each cluster, then sort clusters. */
function canonClusters(clusters: number[][]): number[][] {
  return clusters
    .map((c) => [...c].sort((a, b) => a - b))
    .sort((x, y) => (x[0] ?? 0) - (y[0] ?? 0) || x.length - y.length);
}

/** Canonical edge set: a<=b within each edge, scores 4dp, list sorted by (a,b). */
function canonEdges(r: Resolution) {
  return r.edges
    .map((e) => {
      const [a, b] = e.a <= e.b ? [e.a, e.b] : [e.b, e.a];
      return {
        a,
        b,
        score: {
          name: round4(e.score.name),
          category: round4(e.score.category),
          anchor: round4(e.score.anchor),
          embedding: round4(e.score.embedding),
          attribute_bonus: round4(e.score.attribute_bonus),
          gated_in: e.score.gated_in,
          score: round4(e.score.score),
        },
      };
    })
    .sort((x, y) => x.a - y.a || x.b - y.b);
}

describe("goldenprofile wasm <-> host parity", () => {
  beforeAll(() => {
    enableGoldenprofileWasm();
  });

  it("has fixtures to assert", () => {
    expect(fixtures.cases.length).toBeGreaterThan(0);
  });

  for (const c of fixtures.cases) {
    it(`case: ${c.name}`, () => {
      const got = resolveProfiles(c.request);
      // Same partition (order-independent).
      expect(canonClusters(got.clusters)).toEqual(canonClusters(c.expected.clusters));
      // Same edge set + scores to 4dp (order-independent).
      expect(canonEdges(got)).toEqual(canonEdges(c.expected));
    });
  }
});
