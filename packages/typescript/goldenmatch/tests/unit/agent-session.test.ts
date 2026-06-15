import { describe, it, expect } from "vitest";
import { AgentSession } from "../../src/core/agent/session.js";
import type { Row } from "../../src/core/types.js";

const peopleRows = (): Row[] => [
  { id: "1", name: "John Smith", city: "NYC" },
  { id: "2", name: "Jon Smith", city: "NYC" },
  { id: "3", name: "Zeke Xavier", city: "LA" },
  { id: "4", name: "Mary Jones", city: "SF" },
];

describe("AgentSession.autoconfigure", () => {
  it("returns a config + telemetry with source 'autoconfigure'", async () => {
    const session = new AgentSession();
    const out = await session.autoconfigure(peopleRows());
    expect(out.config).toBeDefined();
    expect(out.telemetry.available).toBe(true);
    expect(out.telemetry.source).toBe("autoconfigure");
    expect(session.config).toBe(out.config);
    expect(session.lastTelemetry).toBe(out.telemetry);
  });
});

describe("AgentSession.deduplicate", () => {
  it("returns four confidence_distribution keys + memory storage", async () => {
    const session = new AgentSession();
    const out = await session.deduplicate(peopleRows());

    const cd = out.confidence_distribution;
    expect(cd).toHaveProperty("auto_merged");
    expect(cd).toHaveProperty("review");
    expect(cd).toHaveProperty("auto_rejected");
    expect(cd).toHaveProperty("total_pairs");

    // total_pairs == sum of the three buckets and == scoredPairs length.
    const sum = cd.auto_merged + cd.review + cd.auto_rejected;
    expect(sum).toBe(cd.total_pairs);

    expect(out.storage).toBe("memory");
    expect(out.reasoning).toBeDefined();
    expect(out.results).toBeDefined();
  });

  it("sets last_telemetry to {available:false, source:'deduplicate'}", async () => {
    const session = new AgentSession();
    await session.deduplicate(peopleRows());
    expect(session.lastTelemetry).toEqual({
      available: false,
      source: "deduplicate",
    });
  });

  it("accepts an explicit config", async () => {
    const session = new AgentSession();
    const cfg = await session.autoconfigure(peopleRows());
    const out = await session.deduplicate(peopleRows(), cfg.config);
    expect(out.confidence_distribution.total_pairs).toBeGreaterThanOrEqual(0);
  });
});

describe("AgentSession.matchSources", () => {
  it("returns results + reasoning", async () => {
    const session = new AgentSession();
    // Source A needs a fuzzy-matchable column so auto-config keeps a matchkey
    // (an all-unique id/email source drops every exact matchkey -> error).
    const a: Row[] = peopleRows();
    const b: Row[] = [
      { id: "5", name: "John Smith", city: "NYC" },
      { id: "6", name: "Mary Jones", city: "SF" },
    ];
    const out = await session.matchSources(a, b);
    expect(out.results).toBeDefined();
    expect(out.reasoning).toBeDefined();
  });
});

describe("AgentSession.compareStrategies", () => {
  it("returns a recommended strategy and per-strategy metrics", async () => {
    const session = new AgentSession();
    const out = await session.compareStrategies(peopleRows());
    expect(out.recommended).toBeDefined();
    expect(out.strategies).toBeDefined();
    // The recommended strategy is always present in the per-strategy map.
    expect(Object.keys(out.strategies)).toContain(out.recommended);
  });
});
