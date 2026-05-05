/**
 * memory-postflight.test.ts -- Phase 2.3: Memory line rendering in postflight.
 */
import { describe, it, expect } from "vitest";
import {
  renderMemoryLine,
  renderPostflight,
  type PostflightReport,
} from "../../src/core/autoconfigVerify.js";
import type { CorrectionStats } from "../../src/core/memory/types.js";

const EMPTY_REPORT: PostflightReport = Object.freeze({
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  signals: {} as any,
  adjustments: [],
  advisories: [],
});

describe("renderMemoryLine", () => {
  it("returns null when stats is null", () => {
    expect(renderMemoryLine(null)).toBeNull();
  });

  it("returns null when every counter is zero", () => {
    const s: CorrectionStats = {
      applied: 0,
      stale: 0,
      staleAmbiguous: 0,
      staleUnanchorable: 0,
      stalePairs: [],
      totalPairs: 0,
    };
    expect(renderMemoryLine(s)).toBeNull();
  });

  it("renders Memory line with singular noun when applied=1", () => {
    const s: CorrectionStats = {
      applied: 1,
      stale: 0,
      staleAmbiguous: 0,
      staleUnanchorable: 0,
      stalePairs: [],
      totalPairs: 1,
    };
    const out = renderMemoryLine(s);
    expect(out).not.toBeNull();
    expect(out).toContain("Memory: 1 correction applied");
  });

  it("renders Memory line with plural noun when applied>1", () => {
    const s: CorrectionStats = {
      applied: 3,
      stale: 1,
      staleAmbiguous: 2,
      staleUnanchorable: 0,
      stalePairs: [],
      totalPairs: 6,
    };
    const out = renderMemoryLine(s)!;
    expect(out).toContain("Memory: 3 corrections applied");
    expect(out).toContain("1 stale");
    expect(out).toContain("2 stale-ambiguous");
    expect(out).toContain("0 unanchorable");
  });

  it("renders FAILED line when stats.failed", () => {
    const s: CorrectionStats = {
      applied: 0,
      stale: 0,
      staleAmbiguous: 0,
      staleUnanchorable: 0,
      stalePairs: [],
      totalPairs: 0,
      failed: true,
      error: "db locked",
    };
    expect(renderMemoryLine(s)).toBe("Memory: FAILED -- db locked");
  });

  it("includes staleAmbiguous when only ambiguous counter is non-zero", () => {
    const s: CorrectionStats = {
      applied: 0,
      stale: 0,
      staleAmbiguous: 5,
      staleUnanchorable: 0,
      stalePairs: [],
      totalPairs: 5,
    };
    const out = renderMemoryLine(s)!;
    expect(out).toContain("5 stale-ambiguous");
  });
});

describe("renderPostflight", () => {
  it("omits Memory line when stats absent", () => {
    const out = renderPostflight(EMPTY_REPORT, null);
    expect(out).not.toContain("Memory:");
  });

  it("includes Memory line when applied > 0", () => {
    const s: CorrectionStats = {
      applied: 2,
      stale: 0,
      staleAmbiguous: 0,
      staleUnanchorable: 0,
      stalePairs: [],
      totalPairs: 2,
    };
    const out = renderPostflight(EMPTY_REPORT, s);
    expect(out).toContain("Memory: 2 corrections applied");
  });
});
