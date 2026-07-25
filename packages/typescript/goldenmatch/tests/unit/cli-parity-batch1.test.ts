/**
 * cli-parity-batch1.test.ts -- TS CLI parity batch 1: `analyze-blocking`,
 * `autoconfig`, `lineage`, and `explain`. These four closed Python-only gaps in
 * `parity/goldenmatch.yaml` (cli_commands), and each is thin wiring over a core
 * that already existed in TS.
 *
 * Per repo convention (cli-memory / cli-evaluate / cli-compare-incremental) we
 * test the core logic each subcommand wraps -- plus the small amount of real
 * logic that lives in the command itself (the matchkey-field derivation
 * `analyze-blocking` does) -- rather than driving the commander tree.
 */
import { describe, it, expect } from "vitest";
import { analyzeBlocking } from "../../src/core/block-analyzer.js";
import { autoConfigure } from "../../src/core/autoconfig.js";
import { buildLineage, explainCluster, explainPair } from "../../src/core/index.js";
import { dedupe } from "../../src/core/api.js";
import type { GoldenMatchConfig, Row } from "../../src/core/types.js";

// Surnames spread across distinct soundex codes -- a same-code fixture makes
// blocking degenerate (one giant block) and is slow/meaningless to analyze.
const ROWS: Row[] = [
  { id: "1", name: "Alice Nguyen", email: "a@x.com", city: "Boston" },
  { id: "2", name: "Alice Nguyen", email: "a@x.com", city: "Boston" },
  { id: "3", name: "Bob Okafor", email: "b@y.com", city: "Denver" },
  { id: "4", name: "Carol Petrov", email: "c@z.com", city: "Austin" },
  { id: "5", name: "Dave Quinn", email: "d@w.com", city: "Fresno" },
];

/** Mirrors the field-derivation the `analyze-blocking` command performs. */
function matchkeyColumns(cfg: GoldenMatchConfig): string[] {
  return [
    ...new Set(
      (cfg.matchkeys ?? []).flatMap((mk) =>
        (mk.fields ?? []).map((f) => f.field).filter((f): f is string => !!f),
      ),
    ),
  ];
}

describe("analyze-blocking command logic", () => {
  it("derives de-duplicated matchkey columns from a config", () => {
    const cfg = {
      matchkeys: [
        { name: "a", type: "fuzzy", fields: [{ field: "name" }, { field: "city" }] },
        { name: "b", type: "exact", fields: [{ field: "name" }] }, // repeat
      ],
    } as unknown as GoldenMatchConfig;
    // "name" appears twice across matchkeys but must be analyzed once.
    expect(matchkeyColumns(cfg)).toEqual(["name", "city"]);
  });

  it("suggests blocking strategies with sane group statistics", () => {
    const suggestions = analyzeBlocking(ROWS, ["name", "city", "email"]);
    expect(suggestions.length).toBeGreaterThan(0);
    for (const s of suggestions) {
      expect(s.group_count).toBeGreaterThan(0);
      expect(s.max_group_size).toBeGreaterThanOrEqual(1);
      expect(s.estimated_recall).toBeGreaterThanOrEqual(0);
      expect(s.estimated_recall).toBeLessThanOrEqual(1);
      expect(typeof s.description).toBe("string");
    }
  });
});

describe("autoconfig command logic", () => {
  it("derives a config with at least one matchkey from raw rows", () => {
    const cfg = autoConfigure(ROWS);
    const mks = cfg.matchkeys ?? [];
    expect(mks.length).toBeGreaterThan(0);
    // every derived matchkey is printable by the command (name + type + fields)
    for (const mk of mks) {
      expect(typeof mk.type).toBe("string");
      expect(Array.isArray(mk.fields)).toBe(true);
    }
  });
});

describe("lineage command logic", () => {
  it("builds a lineage bundle over a dedupe result", async () => {
    const result = await dedupe(ROWS, { exact: ["email"] });
    const bundle = buildLineage(result);
    expect(Array.isArray(bundle.edges)).toBe(true);
    expect(typeof bundle.timestamp).toBe("string");
    // the two identical a@x.com rows matched, so there is at least one edge
    expect(bundle.edges.length).toBeGreaterThan(0);
    // NOTE: `recordCount` is a misnomer in the TS bundle -- core/lineage.ts sets
    // it to `edges.length`, NOT the input row count. Locked here so the CLI's
    // reporting stays honest (Python's build_lineage returns a bare list of edge
    // dicts and has no equivalent field, so this is TS-internal, not a parity gap).
    expect(bundle.recordCount).toBe(bundle.edges.length);
  });
});

describe("explain command logic", () => {
  it("explains a matched pair with a score and reasoning", async () => {
    const result = await dedupe(ROWS, { exact: ["email"] });
    const mk = (result.config.matchkeys ?? [])[0]!;
    const ex = explainPair(ROWS[0]!, ROWS[1]!, mk);
    expect(ex.score).toBeGreaterThan(0);
    expect(typeof ex.explanation).toBe("string");
    expect(["high", "medium", "low"]).toContain(ex.confidence);
    expect(Array.isArray(ex.reasoning)).toBe(true);
  });

  it("summarizes a cluster produced by the run", async () => {
    const result = await dedupe(ROWS, { exact: ["email"] });
    const mk = (result.config.matchkeys ?? [])[0]!;
    const [clusterId, cluster] = [...result.clusters.entries()][0]!;
    const ex = explainCluster(clusterId, cluster, ROWS, mk);
    expect(ex.clusterId).toBe(clusterId);
    expect(ex.size).toBeGreaterThan(0);
    expect(typeof ex.summary).toBe("string");
  });
});
