/**
 * Tests for the A2A skill dispatch functions.
 * Ported from tests/test_a2a_skills.py. Calls dispatchSkill() directly.
 */
import { describe, it, expect } from "vitest";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { dispatchSkill, extractParams } from "../../../src/node/a2a/server.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const SIMPLE_CSV = join(__dirname, "..", "..", "fixtures", "simple.csv");

type Dict = Record<string, unknown>;

function makeMessage(data: Dict): Dict {
  return { role: "user", parts: [{ type: "data", data }] };
}

function call(skill: string, data: Dict): Dict {
  const params = extractParams(makeMessage(data));
  return dispatchSkill(skill, params) as Dict;
}

describe("A2A skill dispatch", () => {
  it("extractParams pulls params from the first data part", () => {
    expect(extractParams(makeMessage({ file_path: "x" }))).toEqual({ file_path: "x" });
    // Plain dict without wrapper
    expect(extractParams({ parts: [{ file_path: "y" }] })).toEqual({ file_path: "y" });
    expect(extractParams({})).toEqual({});
  });

  it("scan", () => {
    const r = call("scan", { file_path: SIMPLE_CSV });
    expect(r.error).toBeUndefined();
    expect(Array.isArray(r.findings)).toBe(true);
    expect(r.health).toBeDefined();
    expect((r.health as Dict).grade).toBeDefined();
    expect((r.health as Dict).score).toBeDefined();
    expect(r.row_count as number).toBeGreaterThan(0);
    expect(r.column_count as number).toBeGreaterThan(0);
  });

  it("validate with no config returns an error gracefully", () => {
    const r = call("validate", { file_path: SIMPLE_CSV, config_path: "nonexistent.yml" });
    expect(r.error).toBeDefined();
    expect(String(r.error).toLowerCase()).toMatch(/not found|config/);
  });

  it("analyze_data", () => {
    const r = call("analyze_data", { file_path: SIMPLE_CSV });
    expect(r.error).toBeUndefined();
    expect("domain" in r).toBe(true);
    expect("sample_strategy" in r || "profiler_strategy" in r).toBe(true);
    expect("alternatives" in r).toBe(true);
  });

  it("configure", () => {
    const r = call("configure", { file_path: SIMPLE_CSV });
    expect(r.error).toBeUndefined();
    expect(typeof r.config).toBe("object");
    expect("version" in (r.config as Dict)).toBe(true);
    expect("columns" in (r.config as Dict)).toBe(true);
    expect("pinned_count" in r).toBe(true);
    expect("review_count" in r).toBe(true);
    expect("dismissed_count" in r).toBe(true);
  });

  it("explain a real finding", () => {
    const scanResult = call("scan", { file_path: SIMPLE_CSV });
    const findings = scanResult.findings as Dict[];
    expect(findings.length).toBeGreaterThan(0);
    const first = findings[0]!;
    const r = call("explain", {
      file_path: SIMPLE_CSV,
      column: first.column,
      check: first.check,
    });
    expect(r.error).toBeUndefined();
  });

  it("review (empty job) returns empty pending + stats", () => {
    const r = call("review", { action: "list", job_name: "no-such-job-xyz" });
    expect(r.error).toBeUndefined();
    expect(r.pending).toEqual([]);
    expect("stats" in r).toBe(true);
  });

  it("compare_domains", () => {
    const r = call("compare_domains", { file_path: SIMPLE_CSV });
    expect(r.error).toBeUndefined();
    expect(typeof r).toBe("object");
  });

  it("fix (safe mode)", () => {
    const r = call("fix", { file_path: SIMPLE_CSV, mode: "safe" });
    expect(r.error).toBeUndefined();
    expect(r.mode).toBe("safe");
    expect("total_rows_fixed" in r).toBe(true);
    expect(Array.isArray(r.fixes)).toBe(true);
  });

  it("handoff produces an attestation", () => {
    const r = call("handoff", { file_path: SIMPLE_CSV });
    expect(r.error).toBeUndefined();
    expect("attestation" in r).toBe(true);
  });

  it("unknown skill returns an error", () => {
    const r = call("totally_bogus_skill", { file_path: SIMPLE_CSV });
    expect(r.error).toBeDefined();
    expect(String(r.error)).toContain("Unknown skill");
  });

  it("missing file_path returns an error for file-based skills", () => {
    expect((dispatchSkill("scan", {}) as Dict).error).toBeDefined();
    expect((dispatchSkill("fix", {}) as Dict).error).toBeDefined();
    expect((dispatchSkill("handoff", {}) as Dict).error).toBeDefined();
  });
});
