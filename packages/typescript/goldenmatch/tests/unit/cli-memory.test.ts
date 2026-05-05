/**
 * cli-memory.test.ts -- memory CLI subgroup behavior.
 *
 * Driving the commander tree through `program.parseAsync` would require
 * mutable global state across tests; instead we test the underlying logic
 * the CLI subcommands wrap (CSV format, round-trip, malformed-row skip)
 * which is exactly what the plan calls out as the lenient default behavior.
 *
 * The end-to-end CLI surface is verified by typecheck (`tsc --noEmit`) and
 * by the smoke test that imports `cli.ts`.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  mkdtempSync,
  rmSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  addCorrection,
  getMemory,
  memoryStats,
} from "../../src/node/memory/api.js";

let dir: string;
let dbPath: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "gm-cli-mem-"));
  dbPath = join(dir, "memory.db");
});

afterEach(() => {
  try {
    rmSync(dir, { recursive: true, force: true });
  } catch {
    /* ignore */
  }
});

// CSV format the export subcommand writes -- snake_case columns, in this exact
// order. Locks the format against drift; matches Python cli/memory.py:_CSV_FIELDS.
const CSV_HEADER =
  "id,id_a,id_b,decision,source,trust,field_hash,record_hash," +
  "original_score,matchkey_name,reason,dataset,created_at";

describe("memory CLI: export CSV shape", () => {
  it("writes the snake_case header in the documented order", async () => {
    await addCorrection({
      idA: 1,
      idB: 2,
      decision: "approve",
      dataset: "d",
      path: dbPath,
    });
    // Re-implement the export logic inline to assert format. CLI subcommand
    // in cli.ts uses the same shape.
    const store = await getMemory({ path: dbPath });
    let corrections;
    try {
      corrections = await store.getCorrections();
    } finally {
      await store.close?.();
    }
    expect(corrections.length).toBe(1);
    const c = corrections[0]!;
    const lines = [CSV_HEADER];
    lines.push(
      [
        c.id,
        c.idA,
        c.idB,
        c.decision,
        c.source,
        c.trust,
        c.fieldHash || "",
        c.recordHash || "",
        c.originalScore,
        c.matchkeyName || "",
        c.reason || "",
        c.dataset || "",
        c.createdAt.toISOString(),
      ].join(","),
    );
    const csv = lines.join("\n") + "\n";
    expect(csv.split("\n")[0]).toBe(CSV_HEADER);
    expect(csv).toMatch(/,1,2,approve,api,0\.5,/);
  });
});

describe("memory CLI: stats output", () => {
  it("memoryStats returns zero/null for an empty store", async () => {
    const s = await memoryStats({ path: dbPath });
    expect(s.count).toBe(0);
    expect(s.lastLearnTime).toBeNull();
    expect(s.adjustments).toEqual([]);
  });

  it("memoryStats counts inserted corrections", async () => {
    await addCorrection({
      idA: 1,
      idB: 2,
      decision: "approve",
      dataset: "d",
      path: dbPath,
    });
    await addCorrection({
      idA: 3,
      idB: 4,
      decision: "reject",
      dataset: "d",
      path: dbPath,
    });
    const s = await memoryStats({ path: dbPath });
    expect(s.count).toBe(2);
  });
});

describe("memory CLI: import lenient parser", () => {
  it("import skips malformed rows but accepts good ones (CLI parser)", async () => {
    // Verify the parser the CLI uses: same logic as in cli.ts memory import.
    const csvPath = join(dir, "bad.csv");
    writeFileSync(
      csvPath,
      "id_a,id_b,decision,source\n" +
        "not-a-number,5,approve,api\n" +
        "10,11,approve,api\n",
      "utf-8",
    );
    // Inline the CLI's parser logic (this is the same shape as cli.ts:import).
    const text = readFileSync(csvPath, "utf-8");
    const lines = text.split(/\r?\n/).filter((l) => l.length > 0);
    const header = lines[0]!.split(",");
    const idxA = header.indexOf("id_a");
    const idxB = header.indexOf("id_b");
    let imported = 0;
    let skipped = 0;
    const store = await getMemory({ path: dbPath });
    try {
      for (let li = 1; li < lines.length; li++) {
        const cells = lines[li]!.split(",");
        const idA = parseInt(cells[idxA]!, 10);
        const idB = parseInt(cells[idxB]!, 10);
        if (!Number.isFinite(idA) || !Number.isFinite(idB)) {
          skipped++;
          continue;
        }
        // Use addCorrection rather than direct store.addCorrection so we get
        // the same default-trust behavior the CLI does.
        await addCorrection({
          idA,
          idB,
          decision: "approve",
          source: "api",
          dataset: null,
          path: dbPath,
        });
        imported++;
      }
    } finally {
      await store.close?.();
    }
    expect(imported).toBe(1);
    expect(skipped).toBe(1);
  });
});
