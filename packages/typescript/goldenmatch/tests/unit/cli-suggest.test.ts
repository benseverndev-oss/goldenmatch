/**
 * cli-suggest.test.ts -- Task 10: the dedupe CLI healer surface.
 *
 * Per repo convention (cli-evaluate.test.ts / cli-memory.test.ts) we test the
 * underlying logic the subcommand wraps -- here the extracted, writer-injected
 * `emitHealerSurface` -- rather than driving the commander tree. The
 * cost-guarantee assertion (no second dedupe for the default hint) is
 * structural: `emitHealerSurface` takes the already-produced result and has no
 * dedupe/pipeline reference, so it CANNOT re-run; the test below proves the
 * end-to-end default `dedupe()` + emit runs the pipeline exactly once.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { emitHealerSurface } from "../../src/node/cli-healer.js";
import type { DedupeResult, SerializedSuggestion } from "../../src/core/types.js";
import type { PostflightReport } from "../../src/core/autoconfigVerify.js";
import { makeConfig } from "../../src/core/types.js";

// Count runDedupePipeline invocations by wrapping the real implementation
// (hoisted, file-scoped). The emitHealerSurface unit tests below never touch
// the pipeline, so the wrapper only matters for the cost-guarantee test.
const { pipelineCalls } = vi.hoisted(() => ({ pipelineCalls: vi.fn() }));
vi.mock("../../src/core/pipeline.js", async (orig) => {
  const actual = await orig<typeof import("../../src/core/pipeline.js")>();
  return {
    ...actual,
    runDedupePipeline: (...args: Parameters<typeof actual.runDedupePipeline>) => {
      pipelineCalls();
      return actual.runDedupePipeline(...args);
    },
  };
});

import { dedupe } from "../../src/core/api.js";

// ---------------------------------------------------------------------------
// fixtures
// ---------------------------------------------------------------------------

function suggestion(over: Partial<SerializedSuggestion> = {}): SerializedSuggestion {
  return {
    id: "thr:raise:person",
    kind: "raise_threshold",
    target: "person",
    rationale: "raise the threshold to drop sub-cutoff noise",
    verified: true,
    patch: { op: "set_threshold", matchkey: "person", value: 0.92 },
    ...over,
  };
}

function postflight(partial: Partial<PostflightReport>): PostflightReport {
  return {
    signals: {
      scoreHistogram: { bins: [], counts: [] },
      blockingRecall: "deferred",
      blockSizePercentiles: { p50: 0, p95: 0, p99: 0, max: 0 },
      thresholdOverlapPct: 0,
      totalPairsScored: 0,
      currentThreshold: 0.5,
      preliminaryClusterSizes: { p50: 0, p95: 0, p99: 0, max: 0, count: 0 },
      oversizedClusters: [],
    },
    adjustments: [],
    advisories: [],
    ...partial,
  };
}

function baseResult(over: Partial<DedupeResult> = {}): DedupeResult {
  return {
    goldenRecords: [],
    clusters: new Map(),
    dupes: [],
    unique: [],
    stats: {
      totalRecords: 2,
      totalClusters: 1,
      matchRate: 0,
      matchedRecords: 0,
      uniqueRecords: 2,
    },
    scoredPairs: [],
    config: makeConfig({}),
    suggestions: [],
    ...over,
  };
}

function writers() {
  const out: string[] = [];
  const err: string[] = [];
  return {
    out: out,
    err: err,
    writers: { out: (s: string) => out.push(s), err: (s: string) => err.push(s) },
  };
}

afterEach(() => vi.restoreAllMocks());

// ---------------------------------------------------------------------------
// --suggest
// ---------------------------------------------------------------------------

describe("emitHealerSurface --suggest", () => {
  it("prints the serialized suggestions (kind -> rationale) to stdout", () => {
    const w = writers();
    emitHealerSurface(
      baseResult({ suggestions: [suggestion()] }),
      { suggest: true },
      w.writers,
    );
    const text = w.out.join("");
    expect(text).toContain("Config suggestions (1)");
    expect(text).toContain("[raise_threshold]");
    expect(text).toContain("raise the threshold to drop sub-cutoff noise");
    expect(w.err).toHaveLength(0);
  });

  it("prints a healthy note when there are no suggestions", () => {
    const w = writers();
    emitHealerSurface(baseResult({ suggestions: [] }), { suggest: true }, w.writers);
    expect(w.out.join("")).toContain("No config suggestions");
    expect(w.err).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// --heal
// ---------------------------------------------------------------------------

describe("emitHealerSurface --heal", () => {
  it("prints the applied trail + a healed-config note", () => {
    const w = writers();
    emitHealerSurface(
      baseResult({ healTrail: [suggestion()] }),
      { heal: true },
      w.writers,
    );
    const text = w.out.join("");
    expect(text).toContain("Healer applied 1 change(s)");
    expect(text).toContain("[raise_threshold]");
    expect(text).toContain("healed config");
    expect(w.err).toHaveLength(0);
  });

  it("prints a no-change note when the trail is empty", () => {
    const w = writers();
    emitHealerSurface(baseResult({ healTrail: [] }), { heal: true }, w.writers);
    expect(w.out.join("")).toContain("no changes applied");
  });
});

// ---------------------------------------------------------------------------
// default run -- free hint to stderr, only when the trigger fires
// ---------------------------------------------------------------------------

describe("emitHealerSurface default run", () => {
  it("prints the headroom hint to STDERR when the trigger fires", () => {
    const w = writers();
    // Bimodal distribution (no unimodal advisory) => headroomSignal fires.
    emitHealerSurface(
      baseResult({ postflightReport: postflight({ adjustments: [], advisories: [] }) }),
      {},
      w.writers,
    );
    expect(w.out).toHaveLength(0);
    expect(w.err.join("")).toContain("Hint: config may have headroom");
    expect(w.err.join("")).toContain("--suggest");
  });

  it("stays silent when the trigger does NOT fire (unimodal / healthy)", () => {
    const w = writers();
    emitHealerSurface(
      baseResult({
        postflightReport: postflight({
          advisories: ["score distribution is unimodal; threshold cannot be auto-set"],
        }),
      }),
      {},
      w.writers,
    );
    expect(w.out).toHaveLength(0);
    expect(w.err).toHaveLength(0);
  });

  it("stays silent when there is no postflight report", () => {
    const w = writers();
    emitHealerSurface(baseResult({}), {}, w.writers);
    expect(w.err).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// cost guarantee -- the default hint reuses the result, never a second dedupe
// ---------------------------------------------------------------------------

describe("default-run cost guarantee", () => {
  it("dedupe() runs the pipeline once; the hint is emitted off that result", async () => {
    pipelineCalls.mockClear();

    const rows = [
      { email: "a@x.com", name: "alice" },
      { email: "a@x.com", name: "alice" },
      { email: "b@x.com", name: "bob" },
    ];
    const result = await dedupe(rows, { exact: ["email"] });

    // Default dedupe (no suggest/heal) must call the pipeline exactly once
    // (maybeSuggest does not re-run it; only heal/reviewConfig would).
    expect(pipelineCalls).toHaveBeenCalledTimes(1);

    // The emitter reads the same result -- it has no pipeline reference, so it
    // cannot re-run. Emitting must not add a pipeline call.
    const w = writers();
    emitHealerSurface(result, {}, w.writers);
    expect(pipelineCalls).toHaveBeenCalledTimes(1);
  });
});
