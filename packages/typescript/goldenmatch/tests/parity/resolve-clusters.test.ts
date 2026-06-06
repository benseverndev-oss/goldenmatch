/**
 * resolve-clusters.test.ts -- cross-language parity for identity resolveClusters.
 *
 * Replays the Python-generated fixture (scripts/emit_resolve_fixture.py) through
 * the TS port and asserts the same per-run ResolveSummary counts and the same
 * final record->entity grouping. Entity ids are UUIDs, so parity is structural
 * (counts + groupings), not literal-id equality.
 */
import { describe, it, expect, beforeAll } from "vitest";
import {
  InMemoryIdentityStore,
  resolveClusters,
} from "../../src/core/identity/index.js";
import { pairKey } from "../../src/core/cluster.js";
import type { ClusterInfo, Row, PairKey } from "../../src/core/types.js";
import fixture from "./fixtures/resolve-clusters.json" with { type: "json" };

interface FixtureRun {
  run_name: string;
  rows: Array<Record<string, unknown>>;
  clusters: Record<
    string,
    { members: number[]; confidence: number; pair_scores: Array<[number, number, number]> }
  >;
  scored_pairs: Array<[number, number, number]>;
  expected_summary: Record<string, number>;
}

function buildRows(raw: Array<Record<string, unknown>>): Row[] {
  return raw.map((r, i) => ({ ...r, __row_id__: i, __source__: "s" })) as Row[];
}

function buildClusters(
  spec: FixtureRun["clusters"],
): Map<number, ClusterInfo> {
  const out = new Map<number, ClusterInfo>();
  for (const [cid, info] of Object.entries(spec)) {
    const ps = new Map<PairKey, number>();
    for (const [a, b, s] of info.pair_scores) ps.set(pairKey(a, b), s);
    out.set(Number(cid), {
      members: info.members,
      size: info.members.length,
      oversized: false,
      pairScores: ps,
      confidence: info.confidence,
      bottleneckPair: null,
      clusterQuality: "strong",
    });
  }
  return out;
}

const store = new InMemoryIdentityStore();
const runs = fixture.runs as unknown as FixtureRun[];

describe("resolveClusters parity (Python fixture)", () => {
  beforeAll(async () => {
    for (const run of runs) {
      const summary = await resolveClusters({
        clusters: buildClusters(run.clusters),
        rows: buildRows(run.rows),
        store,
        runName: run.run_name,
        matchkeyName: "identity",
        sourcePkCol: fixture.source_pk_col as string,
        emitSingletons: true,
      });
      // Attach the produced summary for per-run assertions.
      (run as FixtureRun & { __actual?: Record<string, number> }).__actual = {
        created: summary.created,
        absorbed_records: summary.absorbedRecords,
        merged: summary.merged,
        split: summary.split,
        edges_added: summary.edgesAdded,
        events_emitted: summary.eventsEmitted,
        records_upserted: summary.recordsUpserted,
        conflicts_flagged: summary.conflictsFlagged,
      };
    }
  });

  it.each(["r1", "r2", "r3"])("per-run summary matches Python (%s)", (name) => {
    const run = runs.find((r) => r.run_name === name)!;
    const actual = (run as FixtureRun & { __actual: Record<string, number> }).__actual;
    expect(actual).toEqual(run.expected_summary);
  });

  it("final record->entity grouping matches Python", async () => {
    const active = await store.listIdentities({ status: "active", limit: 1000 });
    const groups: string[][] = [];
    let edges = 0;
    let events = 0;
    for (const node of active) {
      const recs = await store.getRecordsForEntity(node.entityId);
      groups.push(recs.map((r) => r.recordId).sort());
      edges += (await store.edgesForEntity(node.entityId)).length;
      events += (await store.history(node.entityId, 1000)).length;
    }
    groups.sort((a, b) => (a[0] ?? "").localeCompare(b[0] ?? ""));

    const expected = fixture.expected_final as {
      groups: string[][];
      active_identities: number;
      edges: number;
      events: number;
    };
    expect(groups).toEqual(expected.groups);
    expect(active.length).toBe(expected.active_identities);
    expect(edges).toBe(expected.edges);
    expect(events).toBe(expected.events);
  });
});
