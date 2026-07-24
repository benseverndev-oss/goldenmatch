/**
 * cli-parity-batch4.test.ts -- the run-context CLI commands (parity batch 4):
 * `runs`, `rollback`, `unmerge`, `config`.
 *
 * Per repo convention we test the logic each subcommand wraps. The notable
 * finding this batch: NONE of these needed a new persisted run store --
 * `runs`/`rollback` ride the existing durable `.goldenmatch_runs.json` run log
 * (`node/mcp/run-log.ts`), and `unmerge` operates on EXPORTED CSVs, not a live
 * run, exactly as the Python CLI does.
 */
import { describe, it, expect, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync, existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { saveRunSnapshot, listRuns, rollbackRun } from "../../src/node/mcp/run-log.js";
import { unmergeRecord, unmergeCluster, pairKey } from "../../src/core/cluster.js";
import type { ClusterInfo, PairKey } from "../../src/core/types.js";
import { PresetStore } from "../../src/node/preset-store.js";

const dirs: string[] = [];

/** Scratch dir for plain filesystem work (preset store) -- OS tmpdir is fine. */
function tmp(): string {
  const d = mkdtempSync(join(tmpdir(), "gm-batch4-"));
  dirs.push(d);
  return d;
}

/**
 * Scratch dir for the RUN LOG specifically. `run-log.ts` routes `outputDir`
 * through `sanitizePath`, which JAILS every path to the process CWD (inherited
 * from the MCP surface, where it stops a tool escaping the working dir). An OS
 * tmpdir is therefore rejected -- the run log must live under CWD.
 */
function cwdTmp(): string {
  const d = mkdtempSync(join(process.cwd(), "gm-runlog-"));
  dirs.push(d);
  return d;
}

afterEach(() => {
  for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true });
});

/** Build the cluster map the way the `unmerge` command does from CSV rows. */
function clustersFrom(
  members: Record<number, number[]>,
  pairs: Array<[number, number, number]> = [],
): Map<number, ClusterInfo> {
  const byCid = new Map<number, Map<PairKey, number>>();
  const rowCid = new Map<number, number>();
  for (const [cid, mem] of Object.entries(members))
    for (const m of mem) rowCid.set(m, Number(cid));
  for (const [a, b, s] of pairs) {
    const cid = rowCid.get(a);
    if (cid === undefined || cid !== rowCid.get(b)) continue;
    let m = byCid.get(cid);
    if (!m) {
      m = new Map<PairKey, number>();
      byCid.set(cid, m);
    }
    m.set(pairKey(a, b), s);
  }
  return new Map(
    Object.entries(members).map(([cid, mem]): [number, ClusterInfo] => [
      Number(cid),
      {
        members: mem,
        size: mem.length,
        oversized: false,
        pairScores: byCid.get(Number(cid)) ?? new Map<PairKey, number>(),
        confidence: 1,
        bottleneckPair: null,
        clusterQuality: "strong",
      },
    ]),
  );
}

describe("runs / rollback command logic", () => {
  it("lists a saved run and rolls it back, deleting its output files", () => {
    const d = cwdTmp();
    const out = join(d, "golden.csv");
    writeFileSync(out, "a,b\n1,2\n", "utf-8");

    saveRunSnapshot("run-1", d, {}, {}, [out], null);

    const runs = listRuns(d);
    expect(runs.length).toBe(1);
    expect(runs[0]!.rolled_back).toBe(false);
    expect(runs[0]!.output_files).toContain(out);

    const res = rollbackRun(runs[0]!.run_id, d);
    expect("error" in res).toBe(false);
    if (!("error" in res)) {
      expect(res.status).toBe("rolled_back");
      expect(res.deleted).toContain(out);
    }
    // the file is gone and the run is marked rolled back
    expect(existsSync(out)).toBe(false);
    expect(listRuns(d)[0]!.rolled_back).toBe(true);
  });

  it("reports an unknown run id rather than throwing", () => {
    const d = cwdTmp();
    const res = rollbackRun("nope", d);
    expect("error" in res).toBe(true);
  });

  it("returns an empty list when no run log exists", () => {
    expect(listRuns(cwdTmp())).toEqual([]);
  });
});

describe("unmerge command logic", () => {
  it("pulls a record out and re-clusters the remainder from the supplied pair scores", async () => {
    // 0-1-2 in one cluster; 0-1 strong, 2 attached only via a weak edge.
    const clusters = clustersFrom({ 0: [0, 1, 2] }, [
      [0, 1, 0.95],
      [1, 2, 0.80],
    ]);
    const after = await unmergeRecord(1, clusters, 0.0);
    const all = [...after.values()].map((c) => [...c.members].sort((x, y) => x - y));
    // record 1 is now on its own; the rest are re-clustered from pairScores
    expect(all.some((m) => m.length === 1 && m[0] === 1)).toBe(true);
    expect(all.flat().sort((x, y) => x - y)).toEqual([0, 1, 2]);
  });

  it("shatters a cluster into singletons", async () => {
    const clusters = clustersFrom({ 0: [0, 1, 2] });
    const after = await unmergeCluster(0, clusters);
    const sizes = [...after.values()].map((c) => c.members.length);
    expect(sizes).toEqual([1, 1, 1]);
  });

  it("only accepts intra-cluster pair scores (cross-cluster edges are ignored)", () => {
    // 0,1 in cluster 0; 2 in cluster 1. The 1-2 edge spans clusters.
    const clusters = clustersFrom({ 0: [0, 1], 1: [2] }, [
      [0, 1, 0.9],
      [1, 2, 0.7],
    ]);
    expect(clusters.get(0)!.pairScores.size).toBe(1);
    expect(clusters.get(1)!.pairScores.size).toBe(0);
  });
});

describe("config preset store", () => {
  it("saves, lists, shows, loads and deletes a preset", () => {
    const home = tmp();
    const store = new PresetStore(home);
    const cfgPath = join(tmp(), "goldenmatch.yaml");
    writeFileSync(cfgPath, "threshold: 0.9\n", "utf-8");

    store.save("prod", cfgPath);
    expect(store.listPresets()).toEqual(["prod"]);
    expect(store.show("prod")).toContain("threshold: 0.9");

    const dest = join(tmp(), "out.yaml");
    store.load("prod", dest);
    expect(readFileSync(dest, "utf-8")).toContain("threshold: 0.9");

    store.delete("prod");
    expect(store.listPresets()).toEqual([]);
  });

  it("throws a clear error for a missing preset or source config", () => {
    const store = new PresetStore(tmp());
    expect(() => store.load("nope", join(tmp(), "x.yaml"))).toThrow(/Preset not found/);
    expect(() => store.delete("nope")).toThrow(/Preset not found/);
    expect(() => store.show("nope")).toThrow(/Preset not found/);
    expect(() => store.save("x", join(tmp(), "missing.yaml"))).toThrow(/not found/);
  });

  it("returns an empty list when the preset dir does not exist", () => {
    expect(new PresetStore(join(tmp(), "nope")).listPresets()).toEqual([]);
  });
});
