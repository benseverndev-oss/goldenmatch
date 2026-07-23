import { describe, it, expect, afterEach } from "vitest";
import { mkdtempSync, writeFileSync, rmSync, existsSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { tmpdir } from "node:os";
import { TOOLS, handleTool } from "../../src/node/mcp/server.js";
import {
  RUN_LOG_FILE,
  saveRunSnapshot,
  listRuns,
  rollbackRun,
} from "../../src/node/mcp/run-log.js";

// list_runs / rollback operate on the on-disk .goldenmatch_runs.json log, NOT the
// ephemeral RUN_STORE. sanitizePath jails reads/writes to cwd, so the run log +
// output files live in a temp dir under cwd.
const dirs: string[] = [];
const extraFiles: string[] = [];

function tmp(): string {
  const d = mkdtempSync(join(process.cwd(), "rollback-test-"));
  dirs.push(d);
  return d;
}

/** A run-dir-relative output file we can point a snapshot at. */
function outputFile(dir: string, name: string): string {
  const abs = join(dir, name);
  writeFileSync(abs, "golden,rows\n1,2\n", "utf-8");
  return name; // relative to the run dir, like Python records them
}

afterEach(() => {
  for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true });
  for (const f of extraFiles.splice(0)) rmSync(f, { force: true });
});

describe("MCP rollback tools — registration", () => {
  it("registers list_runs and rollback", () => {
    const names = new Set(TOOLS.map((t) => t.name));
    expect(names.has("list_runs")).toBe(true);
    expect(names.has("rollback")).toBe(true);
  });

  it("rollback tool declares run_id as required", () => {
    const rollback = TOOLS.find((t) => t.name === "rollback")!;
    const schema = rollback.inputSchema as { required?: string[] };
    expect(schema.required).toContain("run_id");
  });
});

describe("list_runs", () => {
  it("returns an empty list when no run log exists", async () => {
    const dir = tmp();
    const res = (await handleTool("list_runs", {
      output_dir: relative(process.cwd(), dir),
    })) as { runs: unknown[] };
    expect(res.runs).toEqual([]);
  });

  it("returns recorded runs after saveRunSnapshot", async () => {
    const dir = tmp();
    outputFile(dir, "golden.csv");
    saveRunSnapshot("run-1", relative(process.cwd(), dir), { threshold: 0.85 }, { clusters: 3 }, [
      "golden.csv",
    ]);
    const res = (await handleTool("list_runs", {
      output_dir: relative(process.cwd(), dir),
    })) as { runs: { run_id: string; rolled_back: boolean }[] };
    expect(res.runs.length).toBe(1);
    expect(res.runs[0]!.run_id).toBe("run-1");
    expect(res.runs[0]!.rolled_back).toBe(false);
  });

  it("keeps only the last 50 runs", () => {
    const dir = tmp();
    for (let i = 0; i < 55; i++) {
      saveRunSnapshot(`run-${i}`, relative(process.cwd(), dir), {}, {}, []);
    }
    const runs = listRuns(relative(process.cwd(), dir));
    expect(runs.length).toBe(50);
    expect(runs[0]!.run_id).toBe("run-5"); // oldest 5 evicted
    expect(runs[49]!.run_id).toBe("run-54");
  });
});

describe("rollback", () => {
  it("deletes the run's output files and marks it rolled back", async () => {
    const dir = tmp();
    outputFile(dir, "golden.csv");
    outputFile(dir, "dupes.csv");
    saveRunSnapshot("run-1", relative(process.cwd(), dir), {}, {}, ["golden.csv", "dupes.csv"]);

    const res = (await handleTool("rollback", {
      run_id: "run-1",
      output_dir: relative(process.cwd(), dir),
    })) as { run_id: string; deleted: string[]; not_found: string[]; status: string };

    expect(res.status).toBe("rolled_back");
    expect(res.deleted.length).toBe(2);
    expect(res.not_found.length).toBe(0);
    expect(existsSync(join(dir, "golden.csv"))).toBe(false);
    expect(existsSync(join(dir, "dupes.csv"))).toBe(false);

    // The log now marks the run rolled back.
    const runs = listRuns(relative(process.cwd(), dir));
    expect(runs[0]!.rolled_back).toBe(true);
    expect(typeof runs[0]!.rolled_back_at).toBe("string");
  });

  it("reports a missing output file under not_found without aborting", async () => {
    const dir = tmp();
    outputFile(dir, "present.csv");
    // "gone.csv" is never created.
    saveRunSnapshot("run-1", relative(process.cwd(), dir), {}, {}, ["present.csv", "gone.csv"]);

    const res = (await handleTool("rollback", {
      run_id: "run-1",
      output_dir: relative(process.cwd(), dir),
    })) as { deleted: string[]; not_found: string[] };
    expect(res.deleted.length).toBe(1);
    expect(res.not_found.length).toBe(1);
  });

  it("returns an error + available_runs when the run id is unknown", async () => {
    const dir = tmp();
    saveRunSnapshot("run-1", relative(process.cwd(), dir), {}, {}, []);
    const res = (await handleTool("rollback", {
      run_id: "does-not-exist",
      output_dir: relative(process.cwd(), dir),
    })) as { error?: string; available_runs?: string[] };
    expect(res.error).toMatch(/not found/i);
    expect(res.available_runs).toEqual(["run-1"]);
  });

  it("refuses to roll back a run twice", async () => {
    const dir = tmp();
    outputFile(dir, "golden.csv");
    saveRunSnapshot("run-1", relative(process.cwd(), dir), {}, {}, ["golden.csv"]);
    rollbackRun("run-1", relative(process.cwd(), dir));
    const res = (await handleTool("rollback", {
      run_id: "run-1",
      output_dir: relative(process.cwd(), dir),
    })) as { error?: string };
    expect(res.error).toMatch(/already rolled back/i);
  });

  it("skips a jailed (outside-cwd) output path instead of deleting it", () => {
    const dir = tmp();
    // An absolute path OUTSIDE cwd (the OS temp dir is on a different root on Windows,
    // and outside the package cwd on POSIX) — sanitizePath must refuse it.
    const outside = join(mkdtempSync(join(tmpdir(), "gm-jail-")), "victim.csv");
    writeFileSync(outside, "do not delete\n", "utf-8");
    extraFiles.push(outside);

    saveRunSnapshot("run-1", relative(process.cwd(), dir), {}, {}, [outside]);
    const res = rollbackRun("run-1", relative(process.cwd(), dir));
    expect("status" in res && res.status).toBe("rolled_back");
    if ("deleted" in res) {
      expect(res.deleted.length).toBe(0);
      expect(res.not_found).toContain(outside);
    }
    // The jailed file was NOT deleted.
    expect(existsSync(outside)).toBe(true);
    rmSync(outside, { force: true });
  });

  it("requires a run_id", async () => {
    const res = (await handleTool("rollback", {})) as { error?: string };
    expect(res.error).toMatch(/run_id/i);
  });
});

describe("run-log file shape", () => {
  it("writes .goldenmatch_runs.json in the output dir", () => {
    const dir = tmp();
    saveRunSnapshot("run-1", relative(process.cwd(), dir), { a: 1 }, { b: 2 }, ["x.csv"], "src.csv");
    const raw = JSON.parse(readFileSync(join(dir, RUN_LOG_FILE), "utf-8")) as Array<{
      run_id: string;
      original_file: string;
      output_files: string[];
      config: unknown;
    }>;
    expect(raw.length).toBe(1);
    expect(raw[0]!.run_id).toBe("run-1");
    expect(raw[0]!.original_file).toBe("src.csv");
    expect(raw[0]!.output_files).toEqual(["x.csv"]);
  });
});
