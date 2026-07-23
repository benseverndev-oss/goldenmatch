import { describe, it, expect, afterEach } from "vitest";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { join, relative } from "node:path";
import { TOOLS, handleTool } from "../../src/node/mcp/server.js";

// sanitizePath in the server restricts reads to within process.cwd(), so the
// fixture files must live under cwd. We create a temp dir there and pass
// cwd-relative paths, mirroring how a caller would reference local files.
let tmpDir: string | null = null;

afterEach(() => {
  if (tmpDir !== null) {
    rmSync(tmpDir, { recursive: true, force: true });
    tmpDir = null;
  }
});

function writeClusters(name: string, data: unknown): string {
  tmpDir ??= mkdtempSync(join(process.cwd(), "cc-mcp-test-"));
  const abs = join(tmpDir, name);
  writeFileSync(abs, JSON.stringify(data), "utf-8");
  return relative(process.cwd(), abs);
}

describe("MCP compare_clusters tool", () => {
  it("is registered in TOOLS with the two path args", () => {
    const tool = TOOLS.find((t) => t.name === "compare_clusters");
    expect(tool).toBeDefined();
    const schema = tool!.inputSchema as {
      properties: Record<string, unknown>;
      required: string[];
    };
    expect(Object.keys(schema.properties).sort()).toEqual([
      "clusters_a_path",
      "clusters_b_path",
    ]);
    expect(schema.required.sort()).toEqual(["clusters_a_path", "clusters_b_path"]);
  });

  it("compares two clusters-JSON files and returns the CCMS summary", async () => {
    // A: {1,2,3}  vs  B: {1,2},{3}  -> partitioned; twi = sqrt(2)/2
    const a = writeClusters("a.json", { "1": { members: [1, 2, 3] } });
    const b = writeClusters("b.json", { "1": [1, 2], "2": [3] });

    const result = (await handleTool("compare_clusters", {
      clusters_a_path: a,
      clusters_b_path: b,
    })) as Record<string, number>;

    expect(result).toEqual({
      unchanged: 0,
      merged: 0,
      partitioned: 1,
      overlapping: 0,
      rc: 3,
      cc1: 1,
      cc2: 2,
      sc1: 0,
      sc2: 1,
      twi: 0.7071,
      unchanged_pct: 0,
      merged_pct: 0,
      partitioned_pct: 1,
      overlapping_pct: 0,
    });
  });

  it("returns a structured error when row coverage differs (never throws)", async () => {
    const a = writeClusters("a.json", { "1": [1, 2] });
    const b = writeClusters("b.json", { "1": [1, 2, 3] });

    const result = (await handleTool("compare_clusters", {
      clusters_a_path: a,
      clusters_b_path: b,
    })) as { error?: string };

    expect(typeof result.error).toBe("string");
  });
});
