/**
 * Unit tests for built-in decision functions.
 */

import { describe, it, expect } from "vitest";
import {
  severityGate,
  piiRouter,
  rowCountGate,
  makePipeContext,
} from "../../src/core/index.js";

describe("severityGate", () => {
  it("returns null with no findings", () => {
    expect(severityGate(makePipeContext())).toBeNull();
  });
  it("aborts on critical finding", () => {
    const ctx = makePipeContext({ artifacts: { findings: [{ severity: "critical" }] } });
    const d = severityGate(ctx);
    expect(d?.abort).toBe(true);
  });
  it("does not abort on info/warning/error (TS GoldenCheck skew)", () => {
    const ctx = makePipeContext({
      artifacts: { findings: [{ severity: "error" }, { severity: "warning" }] },
    });
    expect(severityGate(ctx)).toBeNull();
  });
});

describe("piiRouter", () => {
  it("routes to PPRL on pii_detection check", () => {
    const ctx = makePipeContext({ artifacts: { findings: [{ check: "pii_detection" }] } });
    const d = piiRouter(ctx);
    expect(d?.skip).toEqual(["goldenmatch.dedupe"]);
    expect(d?.insert).toEqual(["goldenmatch.dedupe_pprl"]);
  });
  it("returns null when no PII check present", () => {
    const ctx = makePipeContext({ artifacts: { findings: [{ check: "null_check" }] } });
    expect(piiRouter(ctx)).toBeNull();
  });
});

describe("rowCountGate", () => {
  it("skips dedupe below 2 rows", () => {
    const ctx = makePipeContext({ metadata: { input_rows: 1 } });
    const d = rowCountGate(ctx);
    expect(d?.skip).toEqual(["goldenmatch.dedupe"]);
  });
  it("returns null at >= 2 rows", () => {
    const ctx = makePipeContext({ metadata: { input_rows: 5 } });
    expect(rowCountGate(ctx)).toBeNull();
  });
});
