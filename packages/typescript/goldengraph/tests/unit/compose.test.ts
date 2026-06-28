/**
 * The goldenprofile -> goldengraph bridge: cluster partition -> resolution map,
 * profiles -> mentions, and the full pipeline producing the expected graph.
 */
import { describe, it, expect, afterEach } from "vitest";
import {
  buildGraph,
  communities,
  resolutionFromClusters,
  mentionsFromProfiles,
  disableGoldengraphWasm,
} from "../../src/index.js";
import { enableGoldengraphWasm } from "../../src/core/goldengraphWasm.js";

describe("goldenprofile -> goldengraph compose", () => {
  afterEach(() => {
    disableGoldengraphWasm();
  });

  it("resolutionFromClusters maps each profile index to its cluster id", () => {
    // clusters: entity 0 = profiles {0,1}; entity 1 = profile {2}
    expect(resolutionFromClusters([[0, 1], [2]])).toEqual({ 0: 0, 1: 0, 2: 1 });
  });

  it("mentionsFromProfiles preserves order and maps typ from category", () => {
    const profiles = [
      { name: "Apple Inc", category: "Company", kind: "node" },
      { name: "Tim Cook", category: "Person", kind: "node" },
    ];
    expect(mentionsFromProfiles(profiles)).toEqual([
      { name: "Apple Inc", typ: "Company" },
      { name: "Tim Cook", typ: "Person" },
    ]);
  });

  it("mentionsFromProfiles honors a custom typeOf", () => {
    const profiles = [{ name: "X", category: "Company" }];
    expect(mentionsFromProfiles(profiles, () => "Org")).toEqual([{ name: "X", typ: "Org" }]);
  });

  it("full pipeline: a goldenprofile-shaped resolution builds the expected graph", () => {
    enableGoldengraphWasm();
    // Simulate goldenprofile output: 3 profiles, clusters merge 0+1 into one entity.
    const profiles = [
      { name: "Apple Inc", category: "Company" },
      { name: "Apple", category: "Company" },
      { name: "Tim Cook", category: "Person" },
    ];
    const clusters = [[0, 1], [2]]; // what resolveProfiles(...).clusters would give
    const edges = [{ subj: 2, predicate: "ceo_of", obj: 0, source_ref: "doc1" }];

    const graph = buildGraph(
      mentionsFromProfiles(profiles),
      edges,
      resolutionFromClusters(clusters),
    );

    expect(graph.entities.length).toBe(2);
    const apple = graph.entities.find((e) => e.surface_names.includes("Apple Inc"));
    expect(apple?.surface_names).toEqual(expect.arrayContaining(["Apple", "Apple Inc"]));
    // connected by the ceo_of edge -> one community
    expect(communities(graph)).toEqual([{ id: 0, members: [0, 1] }]);
  });
});
