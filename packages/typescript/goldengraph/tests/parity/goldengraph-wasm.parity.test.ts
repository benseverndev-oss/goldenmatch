/**
 * Cross-surface parity: the wasm kernel must reproduce the host oracle
 * (`goldengraph-core`, the SAME kernel the Python `-native` wheel wraps) on all
 * 4 graph+query ops. Fixtures in `fixtures/goldengraph/queries.json` are authored
 * from the host boundary by `goldengraph-wasm/examples/gen_parity_fixtures.rs`.
 *
 * Graph entity/edge ORDERING can fall out of hash-map order, so both sides are
 * canonicalized (entities by id, edges by subj/pred/obj, members/surface_names/
 * source_refs sorted). Communities are deterministic; seed ids are sorted.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, it, expect, beforeAll } from "vitest";
import {
  buildGraph,
  neighborhood,
  seedsByName,
  communities,
  type Graph,
  type Community,
} from "../../src/index.js";
import { enableGoldengraphWasm } from "../../src/core/goldengraphWasm.js";

interface Case {
  name: string;
  fn: "build_graph" | "neighborhood" | "seeds_by_name" | "communities";
  args: Record<string, unknown>;
  expected: unknown;
}

const here = dirname(fileURLToPath(import.meta.url));
const fixtures: { cases: Case[] } = JSON.parse(
  readFileSync(resolve(here, "fixtures/goldengraph/queries.json"), "utf8"),
);

/** Canonicalize a graph (mirror the Rust generator): order-independent compare. */
function canonGraph(g: Graph): Graph {
  return {
    entities: [...g.entities]
      .map((e) => ({
        ...e,
        members: [...e.members].sort((a, b) => a - b),
        surface_names: [...e.surface_names].sort(),
      }))
      .sort((a, b) => a.entity_id - b.entity_id),
    edges: [...g.edges]
      .map((e) => ({ ...e, source_refs: [...e.source_refs].sort() }))
      .sort(
        (a, b) =>
          a.subj - b.subj ||
          a.predicate.localeCompare(b.predicate) ||
          a.obj - b.obj,
      ),
  };
}

describe("goldengraph wasm <-> host parity", () => {
  beforeAll(() => {
    enableGoldengraphWasm();
  });

  it("has fixtures", () => {
    expect(fixtures.cases.length).toBeGreaterThan(0);
  });

  for (const c of fixtures.cases) {
    it(`case: ${c.name}`, () => {
      switch (c.fn) {
        case "build_graph": {
          const got = buildGraph(c.args.mentions as never, c.args.edges as never, c.args.resolution as never);
          expect(canonGraph(got)).toEqual(canonGraph(c.expected as Graph));
          break;
        }
        case "neighborhood": {
          const got = neighborhood(c.args.graph as Graph, c.args.seeds as number[], c.args.hops as number);
          expect(canonGraph(got)).toEqual(canonGraph(c.expected as Graph));
          break;
        }
        case "seeds_by_name": {
          const got = seedsByName(c.args.graph as Graph, c.args.name as string);
          expect([...got].sort((a, b) => a - b)).toEqual(c.expected as number[]);
          break;
        }
        case "communities": {
          const got = communities(c.args.graph as Graph);
          expect(got).toEqual(c.expected as Community[]);
          break;
        }
      }
    });
  }
});
