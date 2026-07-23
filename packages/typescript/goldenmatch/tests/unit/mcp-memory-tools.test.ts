/**
 * mcp-memory-tools.test.ts -- Five MCP memory tools.
 *
 * Asserts (a) the static surface (names, schemas), (b) the description-string
 * count invariant in server.ts, and (c) end-to-end add_correction ->
 * list_corrections round-trip.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  MEMORY_TOOLS,
  MEMORY_TOOL_NAMES,
  handleMemoryTool,
} from "../../src/node/mcp/memory-tools.js";
import { TOOLS, handleTool } from "../../src/node/mcp/server.js";

let dir: string;
let dbPath: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "gm-mcp-mem-"));
  dbPath = join(dir, "memory.db");
});

afterEach(() => {
  try {
    rmSync(dir, { recursive: true, force: true });
  } catch {
    /* ignore */
  }
});

describe("MEMORY_TOOLS surface", () => {
  it("exports exactly 6 named tools", () => {
    expect(MEMORY_TOOLS.length).toBe(6);
    const names = MEMORY_TOOLS.map((t) => t.name).sort();
    expect(names).toEqual([
      "add_correction",
      "learn_thresholds",
      "list_corrections",
      "memory_export",
      "memory_import",
      "memory_stats",
    ]);
  });

  it("MEMORY_TOOL_NAMES matches MEMORY_TOOLS", () => {
    expect([...MEMORY_TOOL_NAMES].sort()).toEqual(
      MEMORY_TOOLS.map((t) => t.name).sort(),
    );
  });

  it("each tool has a non-empty description and inputSchema", () => {
    for (const t of MEMORY_TOOLS) {
      expect(t.description.length).toBeGreaterThan(20);
      expect(t.inputSchema).toBeTypeOf("object");
      expect((t.inputSchema as { type?: string }).type).toBe("object");
    }
  });
});

describe("server.ts TOOLS count parity", () => {
  it("includes the 5 memory tools", () => {
    const names = new Set(TOOLS.map((t) => t.name));
    for (const m of MEMORY_TOOLS) {
      expect(names.has(m.name)).toBe(true);
    }
  });

  it("server description literal claims the actual TOOLS.length", () => {
    // Read the source file and check the header comment count parses out.
    const src = readFileSync(
      join(__dirname, "..", "..", "src", "node", "mcp", "server.ts"),
      "utf-8",
    );
    const match = src.match(/Exposes (\d+) tools/);
    expect(match).not.toBeNull();
    const claimed = parseInt(match![1]!, 10);
    expect(claimed).toBe(TOOLS.length);
  });
});

describe("handleMemoryTool dispatcher", () => {
  it("add_correction then list_corrections round-trips", async () => {
    const addResp = await handleMemoryTool("add_correction", {
      id_a: 11,
      id_b: 12,
      decision: "approve",
      dataset: "test-ds",
      reason: "looks like the same person",
      path: dbPath,
    });
    const addJson = JSON.parse(addResp[0]!.text);
    expect(addJson.status).toBe("ok");
    expect(addJson.id_a).toBe(11);
    expect(addJson.id_b).toBe(12);
    expect(addJson.decision).toBe("approve");
    expect(addJson.source).toBe("agent");
    expect(addJson.trust).toBe(0.5);

    const listResp = await handleMemoryTool("list_corrections", {
      path: dbPath,
    });
    const listJson = JSON.parse(listResp[0]!.text);
    expect(listJson.count).toBe(1);
    expect(listJson.corrections[0].id_a).toBe(11);
    expect(listJson.corrections[0].id_b).toBe(12);
    expect(listJson.corrections[0].decision).toBe("approve");
    expect(listJson.corrections[0].dataset).toBe("test-ds");
  });

  it("add_correction rejects empty dataset", async () => {
    const resp = await handleMemoryTool("add_correction", {
      id_a: 1,
      id_b: 2,
      decision: "approve",
      dataset: "",
      path: dbPath,
    });
    const json = JSON.parse(resp[0]!.text);
    expect(json.error).toMatch(/dataset/);
  });

  it("memory_stats returns total_corrections, last_learn_time, adjustments", async () => {
    await handleMemoryTool("add_correction", {
      id_a: 1,
      id_b: 2,
      decision: "approve",
      dataset: "d",
      path: dbPath,
    });
    const resp = await handleMemoryTool("memory_stats", { path: dbPath });
    const json = JSON.parse(resp[0]!.text);
    expect(json.total_corrections).toBe(1);
    expect(json.last_learn_time).toBeNull();
    expect(json.adjustments).toEqual([]);
  });

  it("memory_export mirrors list_corrections output shape", async () => {
    await handleMemoryTool("add_correction", {
      id_a: 1,
      id_b: 2,
      decision: "approve",
      dataset: "d",
      path: dbPath,
    });
    const resp = await handleMemoryTool("memory_export", { path: dbPath });
    const json = JSON.parse(resp[0]!.text);
    expect(json.count).toBe(1);
    expect(json.corrections[0].id_a).toBe(1);
  });

  it("learn_thresholds returns empty list when below min corrections", async () => {
    const resp = await handleMemoryTool("learn_thresholds", { path: dbPath });
    const json = JSON.parse(resp[0]!.text);
    expect(json.count).toBe(0);
    expect(json.adjustments).toEqual([]);
  });

  it("unknown memory tool returns error", async () => {
    const resp = await handleMemoryTool("nonexistent_tool", {});
    const json = JSON.parse(resp[0]!.text);
    expect(json.error).toMatch(/Unknown memory tool/);
  });

  it("memory_import writes corrections and preserves hashes verbatim", async () => {
    const impResp = await handleMemoryTool("memory_import", {
      path: dbPath,
      corrections: [
        {
          id: "fixed-id-1",
          id_a: 7,
          id_b: 3,
          decision: "reject",
          source: "steward",
          trust: 1.0,
          field_hash: "abc123",
          record_hash: "recA:recB",
          original_score: 0.42,
          matchkey_name: "name_mk",
          reason: "different people",
          dataset: "imp-ds",
          created_at: "2026-01-02T03:04:05.000Z",
        },
      ],
    });
    const impJson = JSON.parse(impResp[0]!.text);
    expect(impJson.imported).toBe(1);

    // memory_export is the inverse -- round-trips the stored row.
    const expResp = await handleMemoryTool("memory_export", { path: dbPath });
    const expJson = JSON.parse(expResp[0]!.text);
    expect(expJson.count).toBe(1);
    const c = expJson.corrections[0];
    // Pair canonicalized to (min, max) by the store.
    expect(c.id_a).toBe(3);
    expect(c.id_b).toBe(7);
    expect(c.decision).toBe("reject");
    expect(c.source).toBe("steward");
    expect(c.trust).toBe(1.0);
    // Hashes preserved VERBATIM -- NOT regenerated.
    expect(c.field_hash).toBe("abc123");
    expect(c.record_hash).toBe("recA:recB");
    expect(c.original_score).toBe(0.42);
    expect(c.matchkey_name).toBe("name_mk");
    expect(c.dataset).toBe("imp-ds");
  });

  it("memory_import trust-upsert ignores a lower-trust incoming correction", async () => {
    const base = {
      id_a: 1,
      id_b: 2,
      decision: "approve",
      dataset: "up-ds",
      field_hash: "fh",
      record_hash: "rh",
    };
    // Seed a high-trust steward correction.
    await handleMemoryTool("memory_import", {
      path: dbPath,
      corrections: [{ ...base, source: "steward", trust: 1.0, reason: "human" }],
    });
    // Lower-trust agent import for the SAME pair must be ignored.
    const resp = await handleMemoryTool("memory_import", {
      path: dbPath,
      corrections: [{ ...base, source: "agent", trust: 0.5, reason: "bot" }],
    });
    // imported counts rows processed (Python parity), even when the upsert skips.
    expect(JSON.parse(resp[0]!.text).imported).toBe(1);

    const expJson = JSON.parse(
      (await handleMemoryTool("memory_export", { path: dbPath }))[0]!.text,
    );
    expect(expJson.count).toBe(1);
    // The high-trust steward row survived; the lower-trust import was dropped.
    expect(expJson.corrections[0].source).toBe("steward");
    expect(expJson.corrections[0].trust).toBe(1.0);
    expect(expJson.corrections[0].reason).toBe("human");
  });

  it("memory_import defaults source to api (trust 0.5) when omitted", async () => {
    const resp = await handleMemoryTool("memory_import", {
      path: dbPath,
      corrections: [{ id_a: 4, id_b: 5, decision: "approve", dataset: "d" }],
    });
    expect(JSON.parse(resp[0]!.text).imported).toBe(1);
    const expJson = JSON.parse(
      (await handleMemoryTool("memory_export", { path: dbPath }))[0]!.text,
    );
    expect(expJson.corrections[0].source).toBe("api");
    expect(expJson.corrections[0].trust).toBe(0.5);
  });
});

describe("server.ts handleTool routes memory tool names", () => {
  it("delegates list_corrections through MEMORY_TOOL_NAMES dispatch", async () => {
    await handleMemoryTool("add_correction", {
      id_a: 50,
      id_b: 51,
      decision: "reject",
      dataset: "via-server",
      path: dbPath,
    });
    const result = (await handleTool("list_corrections", {
      path: dbPath,
    })) as { count: number; corrections: Array<{ id_a: number }> };
    expect(result.count).toBe(1);
    expect(result.corrections[0]!.id_a).toBe(50);
  });
});
