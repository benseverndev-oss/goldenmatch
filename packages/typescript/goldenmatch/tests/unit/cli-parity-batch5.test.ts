/**
 * cli-parity-batch5.test.ts -- `sensitivity` + `pprl` CLI commands (parity batch 5).
 *
 * Per repo convention we test the logic each subcommand wraps, plus the parsing
 * the command itself owns (the `field:start:stop:step` sweep grammar and the
 * PPRL cluster -> CSV row shaping).
 */
import { describe, it, expect } from "vitest";
import { runSensitivitySweep, sweepStabilityReport } from "../../src/core/sensitivity.js";
import type { SweepSpec } from "../../src/core/sensitivity.js";
import { runPPRL } from "../../src/core/pprl/protocol.js";
import { autoConfigure } from "../../src/core/autoconfig.js";
import type { Row } from "../../src/core/types.js";

/** Mirrors the `--sweep` parser in the CLI command. */
function parseSweep(raw: string): SweepSpec | null {
  const parts = raw.split(":");
  if (parts.length !== 4) return null;
  const [field, a, b, c] = parts as [string, string, string, string];
  const nums = [a, b, c].map(parseFloat);
  if (nums.some((n) => !Number.isFinite(n))) return null;
  return { field, start: nums[0]!, stop: nums[1]!, step: nums[2]! };
}

// Surnames spread across distinct soundex codes (a same-code fixture makes
// blocking degenerate and the sweep meaninglessly slow).
const ROWS: Row[] = [
  { id: "1", name: "Alice Nguyen", email: "a@x.com" },
  { id: "2", name: "Alice Nguyen", email: "a@x.com" },
  { id: "3", name: "Bob Okafor", email: "b@y.com" },
  { id: "4", name: "Carol Petrov", email: "c@z.com" },
  { id: "5", name: "Dave Quinn", email: "d@w.com" },
];

describe("sensitivity command: --sweep parsing", () => {
  it("parses field:start:stop:step", () => {
    expect(parseSweep("threshold:0.7:0.9:0.1")).toEqual({
      field: "threshold",
      start: 0.7,
      stop: 0.9,
      step: 0.1,
    });
  });

  it("rejects the wrong arity and non-numeric ranges", () => {
    expect(parseSweep("threshold:0.7:0.9")).toBeNull(); // too few
    expect(parseSweep("threshold:0.7:0.9:0.1:2")).toBeNull(); // too many
    expect(parseSweep("threshold:low:high:step")).toBeNull(); // non-numeric
  });
});

describe("sensitivity command logic", () => {
  it("sweeps a threshold and reports stability against the baseline", async () => {
    const config = autoConfigure(ROWS);
    const results = await runSensitivitySweep(
      ROWS,
      config,
      [{ field: "threshold", start: 0.7, stop: 0.9, step: 0.1 }],
    );
    expect(results.length).toBe(1);
    const r = results[0]!;
    expect(r.param.field).toBe("threshold");
    expect(r.points.length).toBeGreaterThan(0);

    const report = sweepStabilityReport(r);
    expect(typeof report.best_value).toBe("number");
    expect(report.best_unchanged_pct).toBeGreaterThanOrEqual(0);
    expect(report.best_unchanged_pct).toBeLessThanOrEqual(100);
    for (const p of report.points) {
      expect(typeof p.twi).toBe("number");
      expect(p.unchanged).toBeGreaterThanOrEqual(0);
    }
  }, 20000);
});

describe("pprl link command logic", () => {
  const A: Row[] = [
    { name: "alice nguyen", city: "boston" },
    { name: "bob okafor", city: "denver" },
  ];
  const B: Row[] = [
    { name: "alice nguyen", city: "boston" },
    { name: "carol petrov", city: "austin" },
  ];

  it("links the shared record across parties without sharing raw values", () => {
    const res = runPPRL(A, B, {
      fields: ["name", "city"],
      securityLevel: "high",
      protocol: "trusted_third_party",
      threshold: 0.85,
      scorer: "dice",
    });
    expect(res.matchCount).toBeGreaterThan(0);
    expect(res.totalComparisons).toBe(A.length * B.length);
    // the identical alice record should land in a cross-party cluster
    expect(res.clusters.length).toBeGreaterThan(0);
    const parties = new Set(res.clusters[0]!.map((m) => m.party));
    expect(parties.size).toBe(2);
  });

  it("shapes clusters into the CSV rows the command writes", () => {
    const res = runPPRL(A, B, {
      fields: ["name", "city"],
      securityLevel: "high",
      protocol: "trusted_third_party",
      threshold: 0.85,
      scorer: "dice",
    });
    const rows: Row[] = [];
    res.clusters.forEach((members, cid) => {
      for (const m of members) rows.push({ cluster_id: cid, party: m.party, record_id: m.id });
    });
    expect(rows.length).toBeGreaterThan(0);
    for (const r of rows) {
      expect(typeof r["cluster_id"]).toBe("number");
      expect(typeof r["party"]).toBe("string");
      expect(typeof r["record_id"]).toBe("number");
    }
  });

  it("smc reveals only match bits (score == threshold), per the protocol contract", () => {
    const res = runPPRL(A, B, {
      fields: ["name", "city"],
      securityLevel: "high",
      protocol: "smc",
      threshold: 0.85,
      scorer: "dice",
      salt: "shared-key",
    });
    for (const m of res.matches) expect(m.score).toBeCloseTo(0.85, 10);
  });
});
