/**
 * cli-compare-incremental.test.ts -- TS CLI parity: the `compare-clusters` and
 * `incremental` commands. Per repo convention (cli-memory/cli-evaluate) we test
 * the core logic each subcommand wraps rather than driving the commander tree.
 */
import { describe, it, expect } from "vitest";
import {
  compareClusters,
  ccmsSummary,
  parseClustersJson,
} from "../../src/core/compare-clusters.js";
import { runIncremental } from "../../src/core/incremental.js";
import { makeMatchkeyConfig, makeMatchkeyField } from "../../src/core/types.js";
import type { GoldenMatchConfig, Row } from "../../src/core/types.js";

describe("compare-clusters command logic", () => {
  it("parses two cluster JSONs and computes a CCMS summary", () => {
    const a = parseClustersJson({ "0": [0, 1], "1": [2] });
    const b = parseClustersJson({ "0": [0], "1": [1, 2] });
    const s = ccmsSummary(compareClusters(a, b));
    expect(s["rc"]).toBe(3); // total references
    expect(s["cc1"]).toBe(2);
    expect(s["cc2"]).toBe(2);
    expect(typeof s["twi"]).toBe("number");
    // every ER1 cluster is classified into exactly one bucket
    expect(s["unchanged"]! + s["merged"]! + s["partitioned"]! + s["overlapping"]!).toBe(2);
  });

  it("parseClustersJson accepts the {clusters: ...} wrapper + bare-array members", () => {
    const wrapped = parseClustersJson({ clusters: { "0": { members: [0, 1] } } });
    const bare = parseClustersJson({ "0": [0, 1] });
    // both input forms normalize to the same ClusterMembers shape
    expect(wrapped.get(0)).toEqual(bare.get(0));
  });
});

describe("incremental command logic", () => {
  it("matches new records against the base and counts new entities", () => {
    const base: Row[] = [
      { email: "a@x.com", name: "Alice" },
      { email: "b@x.com", name: "Bob" },
    ];
    const newRows: Row[] = [
      { email: "a@x.com", name: "Alice" }, // matches base 0
      { email: "c@x.com", name: "Carol" }, // new entity
    ];
    const config: GoldenMatchConfig = {
      matchkeys: [
        makeMatchkeyConfig({
          name: "email_mk",
          type: "weighted",
          threshold: 0.5,
          fields: [makeMatchkeyField({ field: "email", scorer: "exact", weight: 1.0 })],
        }),
      ],
    };
    const r = runIncremental(base, newRows, config);
    expect(r.new_records).toBe(2);
    expect(r.matched_to_base).toBe(1);
    expect(r.new_entities).toBe(1);
    expect(r.total_pairs).toBeGreaterThanOrEqual(1);
  });
});
