import { describe, it, expect } from "vitest";
import { runDedupePipeline, runMatchPipeline, makeConfig, makeBlockingConfig } from "../../src/core/index.js";
import type { MatchkeyConfig, Row } from "../../src/core/index.js";

describe("runDedupePipeline", () => {
  it.each([
    ["explicit", 0.9, 0.4],
    ["absent review", 0.9, undefined],
    ["calibrated defaults", undefined, undefined],
  ])("attaches probabilistic review candidates with %s thresholds", async (
    _case,
    linkThreshold,
    reviewThreshold,
  ) => {
    const rows: Row[] = [
      { name: "John", zip: "x" },
      { name: "Jon", zip: "x" },
      { name: "John", zip: "x" },
    ];
    const mk: MatchkeyConfig = {
      name: "fs_review",
      type: "probabilistic",
      fields: [{
        field: "name",
        transforms: [],
        scorer: "jaro_winkler",
        weight: 1,
        levels: 3,
        partialThreshold: 0.8,
      }],
      ...(linkThreshold === undefined ? {} : { linkThreshold }),
      ...(reviewThreshold === undefined ? {} : { reviewThreshold }),
    };
    const config = makeConfig({
      matchkeys: [mk],
      blocking: makeBlockingConfig({
        strategy: "static",
        keys: [{ fields: ["zip"], transforms: [] }],
      }),
    });

    const result = await runDedupePipeline(rows, config);

    const reviewCandidates = result.reviewCandidates ?? [];
    expect(result.reviewCandidates).toBeDefined();
    if (linkThreshold === 0.9) {
      expect(reviewCandidates.length).toBeGreaterThan(0);
      expect(result.scoredPairs.every((pair) => pair.score >= 0.9)).toBe(true);
    }
  });

  it("with exact matchkey catches identical emails", async () => {
    const rows: Row[] = [
      { id: 1, email: "a@x.com", name: "Alice" },
      { id: 2, email: "a@x.com", name: "A." },
      { id: 3, email: "b@x.com", name: "Bob" },
    ];
    const mk: MatchkeyConfig = {
      name: "email_exact",
      type: "exact",
      fields: [{ field: "email", transforms: ["lowercase"], scorer: "exact", weight: 1.0 }],
    };
    const config = makeConfig({ matchkeys: [mk] });
    const result = await runDedupePipeline(rows, config);
    expect(result.stats.totalRecords).toBe(3);
    expect(result.scoredPairs.length).toBeGreaterThanOrEqual(1);
    expect(result.dupes.length).toBeGreaterThanOrEqual(2);
  });

  it("with weighted matchkey + blocking", async () => {
    const rows: Row[] = [
      { id: 1, name: "John Smith", zip: "111" },
      { id: 2, name: "Jon Smith", zip: "111" },
      { id: 3, name: "Zeke Xavier", zip: "222" },
    ];
    const mk: MatchkeyConfig = {
      name: "name_fuzzy",
      type: "weighted",
      threshold: 0.7,
      fields: [{ field: "name", transforms: ["lowercase"], scorer: "jaro_winkler", weight: 1.0 }],
    };
    const blocking = makeBlockingConfig({
      strategy: "static",
      keys: [{ fields: ["zip"], transforms: [] }],
    });
    const config = makeConfig({ matchkeys: [mk], blocking });
    const result = await runDedupePipeline(rows, config);
    expect(result.stats.totalRecords).toBe(3);
    // John/Jon should match, Zeke should not
    const hasMatch = result.scoredPairs.some((p) =>
      (p.idA === 0 && p.idB === 1) || (p.idA === 1 && p.idB === 0),
    );
    expect(hasMatch).toBe(true);
  });

  it("empty input returns empty result", async () => {
    const result = await runDedupePipeline([], makeConfig());
    expect(result.stats.totalRecords).toBe(0);
    expect(result.stats.totalClusters).toBe(0);
  });

  it("stats are computed correctly", async () => {
    const rows: Row[] = [
      { id: 1, email: "a@x.com" },
      { id: 2, email: "a@x.com" },
      { id: 3, email: "b@x.com" },
    ];
    const mk: MatchkeyConfig = {
      name: "email",
      type: "exact",
      fields: [{ field: "email", transforms: [], scorer: "exact", weight: 1.0 }],
    };
    const config = makeConfig({ matchkeys: [mk] });
    const result = await runDedupePipeline(rows, config);
    // totalRecords == matchedRecords + uniqueRecords
    expect(result.stats.matchedRecords + result.stats.uniqueRecords).toBe(
      result.stats.totalRecords,
    );
    // matchRate = matchedRecords / totalRecords
    expect(result.stats.matchRate).toBeCloseTo(
      result.stats.matchedRecords / result.stats.totalRecords,
      5,
    );
  });
});

describe("runMatchPipeline", () => {
  it("attaches probabilistic review candidates without marking targets matched", async () => {
    const mk: MatchkeyConfig = {
      name: "fs_review",
      type: "probabilistic",
      fields: [{
        field: "name",
        transforms: [],
        scorer: "jaro_winkler",
        weight: 1,
        levels: 3,
        partialThreshold: 0.8,
      }],
      linkThreshold: 0.9,
      reviewThreshold: 0.4,
    };
    const config = makeConfig({
      matchkeys: [mk],
      blocking: makeBlockingConfig({
        strategy: "static",
        keys: [{ fields: ["zip"], transforms: [] }],
      }),
    });

    const result = await runMatchPipeline(
      [{ name: "Jon", zip: "x" }],
      [{ name: "John", zip: "x" }],
      config,
    );

    expect(result.reviewCandidates?.length).toBeGreaterThan(0);
    expect(result.matched).toHaveLength(0);
    expect(result.unmatched).toHaveLength(1);
  });

  it("finds cross-dataset matches", async () => {
    const target: Row[] = [{ id: 1, email: "a@x.com" }];
    const reference: Row[] = [
      { id: 10, email: "a@x.com" },
      { id: 11, email: "b@x.com" },
    ];
    const mk: MatchkeyConfig = {
      name: "email_exact",
      type: "exact",
      fields: [{ field: "email", transforms: ["lowercase"], scorer: "exact", weight: 1.0 }],
    };
    const config = makeConfig({ matchkeys: [mk] });
    const result = await runMatchPipeline(target, reference, config);
    expect(result.matched.length).toBe(1);
    expect(result.unmatched.length).toBe(0);
  });

  it("empty target yields no matches", async () => {
    const result = await runMatchPipeline([], [{ id: 1, email: "a@x.com" }], makeConfig());
    expect(result.matched).toEqual([]);
    expect(result.unmatched).toEqual([]);
  });

  it("records with no reference match go to unmatched", async () => {
    const target: Row[] = [{ id: 1, email: "no-match@x.com" }];
    const reference: Row[] = [{ id: 10, email: "a@x.com" }];
    const mk: MatchkeyConfig = {
      name: "email_exact",
      type: "exact",
      fields: [{ field: "email", transforms: [], scorer: "exact", weight: 1.0 }],
    };
    const config = makeConfig({ matchkeys: [mk] });
    const result = await runMatchPipeline(target, reference, config);
    expect(result.matched.length).toBe(0);
    expect(result.unmatched.length).toBe(1);
  });
});
