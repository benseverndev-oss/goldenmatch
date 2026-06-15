import { rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { analyzeFile, deduplicateFile } from "../../src/node/agent/session-file.js";

function withCsv(body: string, fn: (path: string) => void | Promise<void>): void | Promise<void> {
  const path = join(tmpdir(), `gm-agent-${process.pid}-${Math.random().toString(36).slice(2)}.csv`);
  writeFileSync(path, body, "utf-8");
  try {
    return fn(path);
  } finally {
    rmSync(path, { force: true });
  }
}

describe("agent file loaders", () => {
  it("analyzeFile profiles a CSV + recommends a strategy", () => {
    withCsv("id,name\n1,John Smith\n2,Jon Smith\n3,Mary Jones\n", (path) => {
      const out = analyzeFile(path);
      expect(out.profile.row_count).toBe(3);
      expect(out.strategy).toBeDefined();
      expect(Array.isArray(out.alternatives)).toBe(true);
    });
  });

  it("deduplicateFile runs the agent pipeline on a CSV", async () => {
    await withCsv("id,name\n1,John Smith\n2,John Smith\n3,Mary Jones\n", async (path) => {
      const out = await deduplicateFile(path);
      expect(out.confidence_distribution).toHaveProperty("total_pairs");
      expect(out.storage).toBe("memory");
    });
  });
});
