/**
 * scheduler.test.ts -- `schedule` command (parity batch 8).
 *
 * `sleep` and `maxRuns` are injectable, so the run LOOP is tested without timers.
 * Python's loop is entangled with `time.sleep` and has no test.
 */
import { describe, it, expect } from "vitest";
import { mkdtempSync, existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { parseInterval, parseCron, ScheduledJob } from "../../src/node/scheduler.js";
import type { Row } from "../../src/core/types.js";

describe("parseInterval (Python parity)", () => {
  it.each([
    ["30s", 30],
    ["30m", 1800],
    ["1h", 3600],
    ["6h", 21600],
    ["1d", 86400],
    ["45", 45], // bare seconds
    [" 2H ", 7200], // trimmed + case-insensitive
  ])("%s -> %i seconds", (spec, expected) => {
    expect(parseInterval(spec)).toBe(expected);
  });

  it.each(["abc", "", "h", "1x", "1.5h"])("rejects %o", (spec) => {
    expect(() => parseInterval(spec)).toThrow(/Invalid interval/);
  });
});

describe("parseCron -- faithful to Python's SIMPLIFIED handling", () => {
  it("requires exactly 5 fields", () => {
    expect(() => parseCron("0 6 * *")).toThrow(/5 fields/);
    expect(() => parseCron("0 6 * * * *")).toThrow(/5 fields/);
  });

  it.each([
    ["0 6 * * *", 86400], // minute AND hour pinned -> daily
    ["30 * * * *", 3600], // minute only -> hourly
    ["* * * * *", 3600], // nothing pinned -> hourly default
  ])("%s -> %i seconds", (spec, expected) => {
    expect(parseCron(spec)).toBe(expected);
  });

  it("is INTERVAL-only, not wall-clock -- the documented shared limitation", () => {
    // "0 6 * * *" means "every 86400s from now", NOT "daily at 06:00". This is
    // Python's behavior; implementing real cron on TS alone would silently diverge
    // a scheduling tool across surfaces. Pinned so the limitation stays visible.
    expect(parseCron("0 6 * * *")).toBe(86400);
    expect(parseCron("0 23 * * *")).toBe(86400); // hour is ignored entirely
    expect(parseCron("0 6 * * 1")).toBe(86400); // weekday ignored too
  });
});

const ROWS: Row[] = [
  { name: "Alice Nguyen", email: "a@x.com" },
  { name: "Alice Nguyen", email: "a@x.com" },
  { name: "Bob Okafor", email: "b@y.com" },
  { name: "Carol Petrov", email: "c@z.com" },
];

// Explicit config: zero-config finds no matches on a 4-row fixture, so the golden
// output would be empty and the "wrote real content" assertion would be vacuous.
const CONFIG = {
  matchkeys: [
    {
      name: "exact_email",
      type: "exact" as const,
      fields: [{ field: "email", transforms: ["lowercase", "strip"], scorer: "exact", weight: 1 }],
    },
  ],
};

function makeJob(dir: string, extra: Record<string, unknown> = {}) {
  return new ScheduledJob({
    jobId: "gm-test123",
    filePaths: ["ignored.csv"],
    outputDir: dir,
    config: CONFIG as never,
    loadRows: () => ROWS,
    ...extra,
  });
}

describe("ScheduledJob.runOnce", () => {
  it("writes a golden CSV and reports stats", async () => {
    const dir = mkdtempSync(join(tmpdir(), "gm-sched-"));
    const job = makeJob(dir);
    const r = await job.runOnce();
    expect(r.runNumber).toBe(1);
    expect(r.totalRecords).toBeGreaterThan(0);
    expect(existsSync(r.outputPath)).toBe(true);
    expect(readFileSync(r.outputPath, "utf-8").length).toBeGreaterThan(0);
    expect(job.lastRun).toBeInstanceOf(Date);
    expect(job.lastResult).toEqual(r);
  }, 20000);

  it("run number is in the filename so successive runs do not clobber", async () => {
    const dir = mkdtempSync(join(tmpdir(), "gm-sched-"));
    const job = makeJob(dir);
    const a = await job.runOnce();
    const b = await job.runOnce();
    expect(a.outputPath).not.toBe(b.outputPath);
    expect(existsSync(a.outputPath)).toBe(true);
    expect(existsSync(b.outputPath)).toBe(true);
    expect(job.runCount).toBe(2);
  }, 25000);

  it("fires onComplete with the summary", async () => {
    const dir = mkdtempSync(join(tmpdir(), "gm-sched-"));
    const seen: number[] = [];
    const job = makeJob(dir, { onComplete: (r: { runNumber: number }) => seen.push(r.runNumber) });
    await job.runOnce();
    await job.runOnce();
    expect(seen).toEqual([1, 2]);
  }, 25000);
});

describe("ScheduledJob.run loop", () => {
  it("runs maxRuns times and sleeps BETWEEN runs, not after the last", async () => {
    const dir = mkdtempSync(join(tmpdir(), "gm-sched-"));
    const sleeps: number[] = [];
    const job = makeJob(dir, { intervalSeconds: 7 });
    await job.run({ maxRuns: 3, sleep: async (ms) => void sleeps.push(ms) });
    expect(job.runCount).toBe(3);
    expect(sleeps).toEqual([7000, 7000]); // 2 sleeps for 3 runs
  }, 40000);

  it("stop() ends the loop", async () => {
    const dir = mkdtempSync(join(tmpdir(), "gm-sched-"));
    const job = makeJob(dir);
    await job.run({
      maxRuns: 10,
      sleep: async () => {
        job.stop();
      },
    });
    expect(job.runCount).toBe(1);
  }, 20000);

  it("a failing run is reported and the schedule CONTINUES", async () => {
    // A scheduled job that dies on one bad input is worse than one that logs on.
    const dir = mkdtempSync(join(tmpdir(), "gm-sched-"));
    let calls = 0;
    const lines: string[] = [];
    const job = new ScheduledJob({
      jobId: "gm-flaky",
      filePaths: ["x.csv"],
      outputDir: dir,
      intervalSeconds: 1,
      loadRows: () => {
        if (++calls === 1) throw new Error("transient read failure");
        return ROWS;
      },
      out: (s) => lines.push(s),
    });
    await job.run({ maxRuns: 2, sleep: async () => {} });
    expect(lines.some((l) => l.includes("run failed: transient read failure"))).toBe(true);
    // maxRuns bounds ATTEMPTS: 2 attempts = 1 failure + 1 success. Bounding
    // SUCCESSES instead would let a permanently-failing job loop forever.
    expect(calls).toBe(2);
    expect(job.runCount).toBe(1);
    expect(lines.at(-1)).toContain("2 attempt(s), 1 successful");
  }, 30000);
});

describe("maxRuns bounds attempts, not successes", () => {
  it("a permanently-failing job still terminates", async () => {
    // The bug this guards: if maxRuns counted only successful runs, this loop
    // would never exit because runCount never advances.
    const job = new ScheduledJob({
      jobId: "gm-broken",
      filePaths: ["x.csv"],
      outputDir: mkdtempSync(join(tmpdir(), "gm-sched-")),
      intervalSeconds: 1,
      loadRows: () => {
        throw new Error("always fails");
      },
    });
    await job.run({ maxRuns: 3, sleep: async () => {} });
    expect(job.runCount).toBe(0); // nothing ever succeeded
  }, 20000);
});
