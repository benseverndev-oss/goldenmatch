/**
 * Reroute equivalence for the shared connected-components primitive
 * (`graphComponents.ts`) and its two former hand-rolled-union-find call sites
 * (`ann-blocker`, `graph-er`). With the graph wasm backend enabled, CC runs the
 * shared `graph-core` kernel; disabled, the pure-TS union-find. Both must
 * produce IDENTICAL components (the CC partition is unique, and the helper
 * returns a canonical order), so the Rust core is the source of truth and the
 * hand-rolled union-finds (a divergence risk) are gone.
 */
import { describe, it, expect, afterEach } from "vitest";
import { connectedComponents } from "../../src/core/graphComponents.js";
import {
  runGraphER,
  type TableSchema,
  type Relationship,
  type GraphERScorer,
} from "../../src/core/graph-er.js";
import { scorePair } from "../../src/core/scorer.js";
import { makeMatchkeyField } from "../../src/core/types.js";
import type { Row, ScoredPair, MatchkeyField, ClusterInfo } from "../../src/core/types.js";
import { enableGraphWasm, disableGraphWasm } from "../../src/core/graphWasm.js";

afterEach(() => disableGraphWasm());

describe("connectedComponents — shared reroute primitive", () => {
  const pairs: [number, number][] = [
    [0, 1], [1, 2], // {0,1,2}
    [4, 5],         // {4,5}
    [7, 8], [8, 9], [9, 7], // {7,8,9} (a cycle)
  ];
  const allIds = Array.from({ length: 11 }, (_, i) => i); // 3,6,10 singletons

  it("returns the correct canonical partition (pure-TS)", () => {
    disableGraphWasm();
    expect(connectedComponents(pairs, allIds)).toEqual([
      [0, 1, 2],
      [3],
      [4, 5],
      [6],
      [7, 8, 9],
      [10],
    ]);
  });

  it("wasm == pure-TS (identical components)", () => {
    disableGraphWasm();
    const pureTs = connectedComponents(pairs, allIds);
    enableGraphWasm();
    const wasm = connectedComponents(pairs, allIds);
    expect(wasm).toEqual(pureTs);
  });

  it("no edges → every id a singleton, both paths", () => {
    disableGraphWasm();
    const a = connectedComponents([], [2, 0, 1]);
    enableGraphWasm();
    const b = connectedComponents([], [2, 0, 1]);
    expect(a).toEqual([[0], [1], [2]]);
    expect(b).toEqual(a);
  });
});

// A projection of clustersByTable that ignores cluster-id numbering and member
// order (only the partition + salient fields matter).
function projectTables(result: {
  clustersByTable: ReadonlyMap<string, ReadonlyMap<number, ClusterInfo>>;
}): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const [table, clusters] of result.clustersByTable) {
    out[table] = [...clusters.values()]
      .map((c) =>
        JSON.stringify({
          members: [...c.members].sort((a, b) => a - b),
          size: c.size,
          confidence: Math.round(c.confidence * 1e6) / 1e6,
          quality: c.clusterQuality,
        }),
      )
      .sort();
  }
  return out;
}

describe("runGraphER — reroute equivalence (wasm == pure-TS)", () => {
  function scenario() {
    const customers: Row[] = [
      { id: 1, name: "John Smith", company_id: 100 },
      { id: 2, name: "Jon Smith", company_id: 100 },
      { id: 3, name: "Jane Doe", company_id: 200 },
      { id: 4, name: "Jane Doe", company_id: 200 },
    ];
    const companies: Row[] = [
      { id: 100, name: "Acme Inc" },
      { id: 200, name: "Widgets LLC" },
    ];
    const tables: TableSchema[] = [
      { name: "customers", rows: customers, idColumn: "id" },
      { name: "companies", rows: companies, idColumn: "id" },
    ];
    const relationships: Relationship[] = [
      { tableA: "customers", tableB: "companies", fkColumn: "company_id" },
    ];
    const nameField = [
      makeMatchkeyField({ field: "name", scorer: "jaro_winkler", transforms: ["lowercase"] }),
    ];
    const allPairsScorer = (fields: readonly MatchkeyField[]): GraphERScorer => (rows) => {
      const pairs: ScoredPair[] = [];
      for (let i = 0; i < rows.length; i++)
        for (let j = i + 1; j < rows.length; j++)
          pairs.push({ idA: i, idB: j, score: scorePair(rows[i]!, rows[j]!, fields) });
      return pairs;
    };
    const scorerByTable = new Map<string, GraphERScorer>([
      ["customers", allPairsScorer(nameField)],
      ["companies", allPairsScorer(nameField)],
    ]);
    return { tables, relationships, scorerByTable };
  }

  it("produces identical clusters with the wasm backend on and off", () => {
    const { tables, relationships, scorerByTable } = scenario();
    const opts = { scorerByTable, threshold: 0.85, maxIterations: 5 };

    disableGraphWasm();
    const pureTs = projectTables(runGraphER(tables, relationships, opts));
    enableGraphWasm();
    const wasm = projectTables(runGraphER(tables, relationships, opts));

    expect(wasm).toEqual(pureTs);
    // The scenario forms a real multi-member cluster (Jane Doe x2), so the
    // reroute is actually exercised, not a trivial all-singletons case.
    expect(pureTs["customers"]!.some((s) => s.includes('"size":2'))).toBe(true);
  });
});
