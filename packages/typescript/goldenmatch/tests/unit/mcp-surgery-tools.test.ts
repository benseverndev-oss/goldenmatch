import { describe, it, expect, afterEach, beforeEach } from "vitest";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { join, relative } from "node:path";
import { TOOLS, handleTool } from "../../src/node/mcp/server.js";
import { RUN_STORE } from "../../src/node/mcp/run-store.js";

// The surgery tools mutate the CURRENT run in RUN_STORE (populated by a `dedupe`
// call). sanitizePath jails reads to cwd, so fixtures live in a temp dir under cwd.
let tmpDir: string | null = null;

function fixture(name: string, contents: string): string {
  tmpDir ??= mkdtempSync(join(process.cwd(), "surgery-tools-test-"));
  const abs = join(tmpDir, name);
  writeFileSync(abs, contents, "utf-8");
  return relative(process.cwd(), abs);
}

// Three rows that all share a name so a single fuzzy/exact matchkey clusters
// them together, giving a 3-member cluster we can operate on.
const CSV3 = "id,name,city\n1,Alice Smith,NYC\n2,Alice Smith,NYC\n3,Alice Smith,NYC\n";
// A dataset with a clean duplicate PAIR (rows 0 & 1) + a singleton (row 2).
const CSV_PAIR = "id,name,city\n1,Alice Smith,NYC\n2,Alice Smith,NYC\n3,Bob Jones,LA\n";

beforeEach(() => {
  RUN_STORE.clear();
});

afterEach(() => {
  RUN_STORE.clear();
  if (tmpDir !== null) {
    rmSync(tmpDir, { recursive: true, force: true });
    tmpDir = null;
  }
});

async function dedupe(csv: string): Promise<string> {
  const path = fixture("data.csv", csv);
  const dd = (await handleTool("dedupe", { path, exact: ["name"] })) as {
    run_id?: string;
  };
  expect(typeof dd.run_id).toBe("string");
  return dd.run_id!;
}

describe("MCP surgery tools — registration", () => {
  it("registers unmerge_record and shatter_cluster", () => {
    const names = new Set(TOOLS.map((t) => t.name));
    expect(names.has("unmerge_record")).toBe(true);
    expect(names.has("shatter_cluster")).toBe(true);
  });
});

describe("MCP surgery tools — no run loaded", () => {
  it("both tools return a structured 'no run' error before any dedupe", async () => {
    const u = (await handleTool("unmerge_record", { record_id: 0 })) as { error?: string };
    expect(u.error).toMatch(/no run loaded/i);
    const s = (await handleTool("shatter_cluster", { cluster_id: 0 })) as { error?: string };
    expect(s.error).toMatch(/no run loaded/i);
  });
});

describe("unmerge_record", () => {
  it("pulls a record out and re-clusters the remainder via stored pair scores", async () => {
    await dedupe(CSV3);

    // Locate the 3-member cluster + one of its members.
    const list = (await handleTool("list_clusters", {})) as {
      clusters: { cluster_id: number; size: number }[];
    };
    const big = list.clusters.find((c) => c.size === 3)!;
    expect(big).toBeDefined();
    const cluster = (await handleTool("get_cluster", { cluster_id: big.cluster_id })) as {
      members: Record<string, unknown>[];
    };
    // Recover the member row ids from the live run (row ids are internal).
    const run = RUN_STORE.getCurrent()!;
    const memberIds = [...run.result.clusters.get(big.cluster_id)!.members];
    expect(memberIds.length).toBe(3);
    expect(cluster.members.length).toBe(3);
    const pulled = memberIds[0]!;

    const res = (await handleTool("unmerge_record", { record_id: pulled })) as {
      status: string;
      record_id: number;
      new_cluster_id: number;
      new_cluster_size: number;
      total_clusters: number;
    };
    expect(res.status).toBe("unmerged");
    expect(res.record_id).toBe(pulled);
    // The pulled record is now a singleton.
    expect(res.new_cluster_size).toBe(1);

    // The remaining two members re-clustered together (stored pairScores put
    // the other two same-name rows back in ONE cluster of size 2).
    const after = RUN_STORE.getCurrent()!;
    const sizes = [...after.result.clusters.values()]
      .map((c) => c.size)
      .sort((a, b) => a - b);
    expect(sizes).toEqual([1, 2]);
    // The pulled record's new cluster holds only it.
    const singleton = after.result.clusters.get(res.new_cluster_id)!;
    expect(singleton.members).toEqual([pulled]);
    // The other two members are together, without the pulled record.
    const pairCluster = [...after.result.clusters.values()].find((c) => c.size === 2)!;
    expect(pairCluster.members).not.toContain(pulled);
    expect(pairCluster.members.length).toBe(2);
  });

  it("updates the CURRENT run in place, preserving the SAME run id", async () => {
    const runId = await dedupe(CSV3);
    const run = RUN_STORE.getCurrent()!;
    const pulled = [...run.result.clusters.values()].find((c) => c.size === 3)!.members[0]!;

    await handleTool("unmerge_record", { record_id: pulled });

    const after = RUN_STORE.getCurrent()!;
    expect(after.runId).toBe(runId); // same run id -> in-place mutation, not a new run
    // The store still resolves the run by its original id, carrying the new clusters.
    expect(RUN_STORE.get(runId)).not.toBeNull();
    expect(RUN_STORE.get(runId)!.result.clusters.size).toBe(2);
  });

  it("reports 'unmerged' without a new cluster for an unknown record id (Python parity)", async () => {
    await dedupe(CSV_PAIR);
    const res = (await handleTool("unmerge_record", { record_id: 99999 })) as {
      status: string;
      record_id: number;
      new_cluster_id?: number;
    };
    // Python's engine leaves clusters unchanged for a record it can't find, so
    // the record is in no cluster -> the fallback shape (no new_cluster_id).
    expect(res.status).toBe("unmerged");
    expect(res.record_id).toBe(99999);
    expect(res.new_cluster_id).toBeUndefined();
  });

  it("requires record_id", async () => {
    await dedupe(CSV_PAIR);
    const res = (await handleTool("unmerge_record", {})) as { error?: string };
    expect(res.error).toMatch(/record_id is required/i);
  });
});

describe("shatter_cluster", () => {
  it("breaks a cluster's members into singletons and updates the run in place", async () => {
    const runId = await dedupe(CSV3);
    const run = RUN_STORE.getCurrent()!;
    const [cid, info] = [...run.result.clusters.entries()].find(([, c]) => c.size === 3)!;
    const memberIds = [...info.members];

    const res = (await handleTool("shatter_cluster", { cluster_id: cid })) as {
      status: string;
      cluster_id: number;
      records_freed: number;
      total_clusters: number;
    };
    expect(res.status).toBe("shattered");
    expect(res.cluster_id).toBe(cid);
    expect(res.records_freed).toBe(3);

    const after = RUN_STORE.getCurrent()!;
    expect(after.runId).toBe(runId); // in-place: same run id
    // Every remaining cluster is a singleton, and each former member is now its
    // own singleton (the kernel may re-use the freed cluster id -- that's fine).
    expect(after.result.clusters.size).toBe(3);
    const singletonMembers = new Set<number>();
    for (const c of after.result.clusters.values()) {
      expect(c.size).toBe(1);
      singletonMembers.add(c.members[0]!);
    }
    for (const m of memberIds) expect(singletonMembers.has(m)).toBe(true);
    // total_clusters in the response matches the rebuilt run's stats.
    expect(res.total_clusters).toBe(after.result.stats.totalClusters);
  });

  it("reports an unknown cluster id as not found (Python parity)", async () => {
    await dedupe(CSV3);
    const res = (await handleTool("shatter_cluster", { cluster_id: 99999 })) as {
      error?: string;
    };
    expect(res.error).toMatch(/not found/i);
  });
});
