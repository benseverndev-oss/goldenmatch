import { describe, it, expect, afterEach, beforeEach } from "vitest";
import { mkdtempSync, writeFileSync, rmSync, readFileSync, existsSync } from "node:fs";
import { join, relative } from "node:path";
import { TOOLS, handleTool } from "../../src/node/mcp/server.js";
import { RUN_STORE, RunStore, stripInternal } from "../../src/node/mcp/run-store.js";

// The run tools read RUN_STORE, populated by a `dedupe` call. sanitizePath jails
// reads/writes to cwd, so fixtures live in a temp dir under cwd.
let tmpDir: string | null = null;

function fixture(name: string, contents: string): string {
  tmpDir ??= mkdtempSync(join(process.cwd(), "run-tools-test-"));
  const abs = join(tmpDir, name);
  writeFileSync(abs, contents, "utf-8");
  return relative(process.cwd(), abs);
}

// A tiny dataset with an obvious duplicate pair (rows 0 & 1) and a singleton.
const CSV = "id,name,city\n1,Alice Smith,NYC\n2,Alice Smith,NYC\n3,Bob Jones,LA\n";

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

describe("MCP run tools — registration", () => {
  it("registers all six run tools", () => {
    const names = new Set(TOOLS.map((t) => t.name));
    for (const n of [
      "get_stats",
      "list_clusters",
      "get_cluster",
      "get_golden_record",
      "export_results",
      "upload_dataset",
    ]) {
      expect(names.has(n), `missing ${n}`).toBe(true);
    }
  });

  it("does NOT register list_runs (deferred with the rollback subsystem)", () => {
    expect(TOOLS.some((t) => t.name === "list_runs")).toBe(false);
  });
});

describe("MCP run tools — no run loaded", () => {
  it("read tools return a structured 'no run' error before any dedupe", async () => {
    for (const name of ["get_stats", "list_clusters"]) {
      const r = (await handleTool(name, {})) as { error?: string };
      expect(r.error).toMatch(/no run loaded/i);
    }
  });
});

describe("MCP run tools — after a dedupe populates the store", () => {
  it("dedupe returns a run_id and the read tools resolve the run", async () => {
    const path = fixture("data.csv", CSV);
    const dd = (await handleTool("dedupe", {
      path,
      exact: ["name"],
    })) as { run_id?: string; total_clusters?: number };
    expect(typeof dd.run_id).toBe("string");

    // get_stats
    const stats = (await handleTool("get_stats", {})) as Record<string, number>;
    expect(stats.total_records).toBe(3);
    expect(stats.total_pairs).toBeGreaterThanOrEqual(1);
    // sizes: one cluster of 2 (the Alices) + one singleton (Bob)
    expect(stats.max_cluster_size).toBe(2);
    expect(stats.singleton_count).toBeGreaterThanOrEqual(1);

    // list_clusters (default min_size 2) → only the multi-member cluster
    const list = (await handleTool("list_clusters", {})) as {
      clusters: { cluster_id: number; size: number }[];
      total: number;
    };
    expect(list.clusters.length).toBe(1);
    expect(list.clusters[0]!.size).toBe(2);
    const cid = list.clusters[0]!.cluster_id;

    // get_cluster → the two member rows, internal cols stripped
    const cluster = (await handleTool("get_cluster", { cluster_id: cid })) as {
      cluster_id: number;
      size: number;
      members: Record<string, unknown>[];
    };
    expect(cluster.size).toBe(2);
    expect(cluster.members.length).toBe(2);
    for (const m of cluster.members) {
      expect(Object.keys(m).some((k) => k.startsWith("__"))).toBe(false);
      expect(m.name).toBe("Alice Smith");
    }

    // get_golden_record → one golden row for the cluster
    const golden = (await handleTool("get_golden_record", { cluster_id: cid })) as {
      cluster_id: number;
      golden_record: Record<string, unknown>;
    };
    expect(golden.cluster_id).toBe(cid);
    expect(Object.keys(golden.golden_record).some((k) => k.startsWith("__"))).toBe(false);
  });

  it("get_cluster / get_golden_record report missing ids", async () => {
    const path = fixture("data.csv", CSV);
    await handleTool("dedupe", { path, exact: ["name"] });
    const gc = (await handleTool("get_cluster", { cluster_id: 9999 })) as { error?: string };
    expect(gc.error).toMatch(/not found/i);
    const gg = (await handleTool("get_golden_record", { cluster_id: 9999 })) as { error?: string };
    expect(gg.error).toMatch(/no golden record/i);
  });

  it("export_results writes golden records and returns the path + count", async () => {
    const path = fixture("data.csv", CSV);
    await handleTool("dedupe", { path, exact: ["name"] });
    const outRel = join(relative(process.cwd(), tmpDir!), "golden.json");
    const res = (await handleTool("export_results", {
      output_path: outRel,
      format: "json",
    })) as { exported: string; format: string; records: number };
    expect(res.format).toBe("json");
    expect(res.records).toBeGreaterThanOrEqual(1);
    expect(existsSync(res.exported)).toBe(true);
    const written = JSON.parse(readFileSync(res.exported, "utf-8")) as Record<string, unknown>[];
    expect(written.length).toBe(res.records);
    // internal columns are excluded from the export
    for (const row of written) {
      expect(Object.keys(row).some((k) => k.startsWith("__"))).toBe(false);
    }
  });
});

describe("upload_dataset", () => {
  it("stages a base64 file and returns path/bytes/filename", async () => {
    const payload = "id,name\n1,x\n";
    const b64 = Buffer.from(payload, "utf-8").toString("base64");
    const res = (await handleTool("upload_dataset", {
      file_content: b64,
      filename: "my data.csv",
    })) as { path: string; bytes: number; filename: string };
    expect(res.filename).toBe("my_data.csv"); // sanitized
    expect(res.bytes).toBe(Buffer.byteLength(payload));
    expect(existsSync(res.path)).toBe(true);
    expect(readFileSync(res.path, "utf-8")).toBe(payload);
    rmSync(res.path, { force: true });
  });

  it("rejects an oversized upload", async () => {
    process.env.GOLDENMATCH_MCP_MAX_UPLOAD_BYTES = "4";
    try {
      const res = (await handleTool("upload_dataset", {
        file_content: "hello world",
        filename: "big.txt",
        encoding: "text",
      })) as { error?: string };
      expect(res.error).toMatch(/exceeds/i);
    } finally {
      delete process.env.GOLDENMATCH_MCP_MAX_UPLOAD_BYTES;
    }
  });
});

describe("RunStore — bounds & eviction", () => {
  const fakeRun = () =>
    ({
      result: {
        clusters: new Map(),
        goldenRecords: [],
        scoredPairs: [],
        stats: {
          totalRecords: 0,
          totalClusters: 0,
          matchRate: 0,
          matchedRecords: 0,
          uniqueRecords: 0,
        },
      },
      rowsById: new Map(),
      sourcePath: null,
    }) as unknown as Parameters<RunStore["put"]>[0];

  it("keeps only `max` runs, evicting oldest", () => {
    const store = new RunStore({ max: 2, ttlSeconds: 3600, clock: () => 0 });
    const a = store.put(fakeRun());
    const b = store.put(fakeRun());
    const c = store.put(fakeRun());
    expect(store.get(a)).toBeNull(); // evicted
    expect(store.get(b)).not.toBeNull();
    expect(store.getCurrent()?.runId).toBe(c);
  });

  it("expires runs past their TTL", () => {
    let now = 0;
    const store = new RunStore({ max: 8, ttlSeconds: 10, clock: () => now });
    const id = store.put(fakeRun());
    expect(store.get(id)).not.toBeNull();
    now = 11_000; // 11s later, past the 10s TTL
    expect(store.get(id)).toBeNull();
    expect(store.getCurrent()).toBeNull();
  });
});

describe("stripInternal", () => {
  it("drops __-prefixed keys only", () => {
    expect(stripInternal({ a: 1, __row_id__: 5, b: "x" })).toEqual({ a: 1, b: "x" });
  });
});
