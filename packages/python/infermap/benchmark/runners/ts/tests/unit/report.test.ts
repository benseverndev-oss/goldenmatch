import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import {
  buildReport,
  validateReport,
  writeReport,
  type CaseResult,
} from "../../src/report.js";

const REPO_ROOT = resolve(__dirname, "../../../../..");
const SCHEMA_PATH = resolve(REPO_ROOT, "benchmark/report.schema.json");

let tmp: string;
beforeEach(() => { tmp = mkdtempSync(join(tmpdir(), "bench-ts-report-")); });
afterEach(() => { rmSync(tmp, { recursive: true, force: true }); });

function makeResult(opts: Partial<CaseResult> & { caseId: string }): CaseResult {
  return {
    caseId: opts.caseId,
    category: opts.category ?? "valentine",
    subcategory: opts.subcategory ?? "test",
    difficulty: opts.difficulty ?? "easy",
    tags: opts.tags ?? ["alias_dominant"],
    top1: opts.top1 ?? 0.5,
    f1: opts.f1 ?? 0.5,
    mrr: opts.mrr ?? 0.5,
    tp: opts.tp ?? 1,
    fp: opts.fp ?? 1,
    fn: opts.fn ?? 1,
    predictions: opts.predictions ?? [],
    failed: opts.failed ?? false,
    failureReason: opts.failureReason ?? null,
  };
}

describe("buildReport", () => {
  it("minimal report has correct structure", () => {
    const results: CaseResult[] = [
      makeResult({ caseId: "a", f1: 1, top1: 1, mrr: 1, tp: 2, fp: 0, fn: 0 }),
    ];
    const report = buildReport(results, {
      language: "typescript",
      infermapVersion: "0.1.0",
      runnerVersion: "0.1.0",
      durationSeconds: 1.5,
    });
    expect(report.version).toBe(1);
    expect(report.language).toBe("typescript");
    expect(report.infermap_version).toBe("0.1.0");
    expect(report.runner_version).toBe("0.1.0");
    expect(typeof report.ran_at).toBe("string");
    expect(report.duration_seconds).toBe(1.5);
    expect(report.per_case).toHaveLength(1);
    expect(report.failed_cases).toEqual([]);
  });

  it("overall scorecard computed via micro F1 + macro means", () => {
    const results: CaseResult[] = [
      makeResult({ caseId: "a", f1: 1, top1: 1, mrr: 1, tp: 2, fp: 0, fn: 0 }),
      makeResult({ caseId: "b", f1: 0, top1: 0, mrr: 0, tp: 0, fp: 2, fn: 2 }),
    ];
    const report = buildReport(results, {
      language: "typescript", infermapVersion: "0.1.0",
      runnerVersion: "0.1.0", durationSeconds: 1,
    });
    expect(report.scorecard.overall.f1).toBe(0.5);
    expect(report.scorecard.overall.top1).toBe(0.5);
    expect(report.scorecard.overall.n).toBe(2);
  });

  it("by_difficulty slice only includes present keys", () => {
    const results: CaseResult[] = [
      makeResult({ caseId: "a", difficulty: "easy", tp: 1, fp: 0, fn: 0 }),
      makeResult({ caseId: "b", difficulty: "hard", tp: 1, fp: 2, fn: 2 }),
    ];
    const report = buildReport(results, {
      language: "typescript", infermapVersion: "0.1.0",
      runnerVersion: "0.1.0", durationSeconds: 1,
    });
    expect(Object.keys(report.scorecard.by_difficulty).sort()).toEqual(["easy", "hard"]);
    expect(report.scorecard.by_difficulty.medium).toBeUndefined();
  });

  it("by_tag handles multi-tag cases", () => {
    const results: CaseResult[] = [
      makeResult({ caseId: "a", tags: ["alias_dominant", "small"], tp: 1, fp: 0, fn: 0 }),
      makeResult({ caseId: "b", tags: ["small"], tp: 0, fp: 0, fn: 1 }),
    ];
    const report = buildReport(results, {
      language: "typescript", infermapVersion: "0.1.0",
      runnerVersion: "0.1.0", durationSeconds: 1,
    });
    expect(report.scorecard.by_tag.alias_dominant!.n).toBe(1);
    expect(report.scorecard.by_tag.small!.n).toBe(2);
  });

  it("failed cases listed", () => {
    const results: CaseResult[] = [
      makeResult({ caseId: "ok" }),
      makeResult({ caseId: "broken", failed: true, failureReason: "KaboomError: nope" }),
    ];
    const report = buildReport(results, {
      language: "typescript", infermapVersion: "0.1.0",
      runnerVersion: "0.1.0", durationSeconds: 1,
    });
    expect(report.failed_cases).toEqual(["broken"]);
    const broken = report.per_case.find((p) => p.id === "broken")!;
    expect(broken.failed).toBe(true);
    expect(broken.failure_reason).toBe("KaboomError: nope");
  });

  it("empty results produces valid structure", () => {
    const report = buildReport([], {
      language: "typescript", infermapVersion: "0.1.0",
      runnerVersion: "0.1.0", durationSeconds: 0,
    });
    expect(report.scorecard.overall.n).toBe(0);
    expect(report.scorecard.by_difficulty).toEqual({});
    expect(report.per_case).toEqual([]);
    expect(report.failed_cases).toEqual([]);
    expect(report.scorecard.overall.f1).toBe(1);
  });

  it("per_case values rounded to 6 decimals", () => {
    const results: CaseResult[] = [
      makeResult({ caseId: "a", f1: 0.123456789, top1: 0.987654321, mrr: 0.555555555 }),
    ];
    const report = buildReport(results, {
      language: "typescript", infermapVersion: "0.1.0",
      runnerVersion: "0.1.0", durationSeconds: 1,
    });
    expect(report.per_case[0]!.f1).toBe(0.123457);
    expect(report.per_case[0]!.top1).toBe(0.987654);
    expect(report.per_case[0]!.mrr).toBe(0.555556);
  });

  it("per_case expected_n and predicted_n computed from TP/FP/FN", () => {
    const results: CaseResult[] = [
      makeResult({ caseId: "a", tp: 3, fp: 1, fn: 2 }),
    ];
    const report = buildReport(results, {
      language: "typescript", infermapVersion: "0.1.0",
      runnerVersion: "0.1.0", durationSeconds: 1,
    });
    const pc = report.per_case[0]!;
    expect(pc.expected_n).toBe(5);
    expect(pc.predicted_n).toBe(4);
    expect(pc.true_positives).toBe(3);
  });
});

describe("validateReport", () => {
  it("valid report passes", () => {
    const report = buildReport([makeResult({ caseId: "a" })], {
      language: "typescript", infermapVersion: "0.1.0",
      runnerVersion: "0.1.0", durationSeconds: 1,
    });
    expect(() => validateReport(report, SCHEMA_PATH)).not.toThrow();
  });

  it("empty report passes", () => {
    const report = buildReport([], {
      language: "typescript", infermapVersion: "0.1.0",
      runnerVersion: "0.1.0", durationSeconds: 0,
    });
    expect(() => validateReport(report, SCHEMA_PATH)).not.toThrow();
  });

  it("python language passes", () => {
    const report = buildReport([], {
      language: "python", infermapVersion: "0.1.0",
      runnerVersion: "0.1.0", durationSeconds: 0,
    });
    expect(() => validateReport(report, SCHEMA_PATH)).not.toThrow();
  });

  it("invalid report (missing required fields) fails", () => {
    const bad = { version: 1, language: "python" };
    expect(() => validateReport(bad, SCHEMA_PATH)).toThrow();
  });

  it("invalid language fails", () => {
    const report = buildReport([], {
      language: "typescript", infermapVersion: "0.1.0",
      runnerVersion: "0.1.0", durationSeconds: 0,
    });
    (report as any).language = "rust";
    expect(() => validateReport(report, SCHEMA_PATH)).toThrow();
  });
});

describe("writeReport", () => {
  it("writes valid report to disk", () => {
    const report = buildReport([makeResult({ caseId: "a" })], {
      language: "typescript", infermapVersion: "0.1.0",
      runnerVersion: "0.1.0", durationSeconds: 1,
    });
    const out = join(tmp, "report.json");
    writeReport(report, out, SCHEMA_PATH);
    expect(existsSync(out)).toBe(true);
    const loaded = JSON.parse(readFileSync(out, "utf8"));
    expect(loaded.version).toBe(1);
  });

  it("refuses to write invalid report", () => {
    const bad = { version: 1 } as any;
    const out = join(tmp, "report.json");
    expect(() => writeReport(bad, out, SCHEMA_PATH)).toThrow();
    expect(existsSync(out)).toBe(false);
  });
});
