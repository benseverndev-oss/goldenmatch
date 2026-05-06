import { describe, it, expect } from "vitest";
import {
  parseBlockingFromServer,
  serializeBlockingForWire,
} from "../lib/types";

describe("BlockingPayload round-trip", () => {
  it("returns null when server payload is null/undefined", () => {
    expect(parseBlockingFromServer(null)).toBeNull();
    expect(parseBlockingFromServer(undefined)).toBeNull();
  });

  it("splits known fields from unknown extras", () => {
    const raw = {
      strategy: "static",
      keys: [{ fields: ["name"], transforms: ["lowercase"] }],
      max_block_size: 5000,
      // unknown to the workbench but valid on the engine BlockingConfig
      ann_top_k: 20,
      learned_min_recall: 0.95,
    };
    const parsed = parseBlockingFromServer(raw);
    expect(parsed?.strategy).toBe("static");
    expect(parsed?.keys?.[0]?.fields).toEqual(["name"]);
    expect(parsed?.max_block_size).toBe(5000);
    expect(parsed?.extras).toEqual({
      ann_top_k: 20,
      learned_min_recall: 0.95,
    });
  });

  it("omits extras when no unknown fields are present", () => {
    const raw = {
      strategy: "multi_pass",
      keys: [{ fields: ["name"], transforms: [] }],
      passes: [{ fields: ["name"], transforms: ["soundex"] }],
    };
    const parsed = parseBlockingFromServer(raw);
    expect(parsed?.extras).toBeUndefined();
  });

  it("flattens extras back into the wire shape on serialize", () => {
    const wire = serializeBlockingForWire({
      strategy: "static",
      keys: [{ fields: ["zip"], transforms: [] }],
      extras: { ann_top_k: 50, learned_predicate_depth: 3 },
    });
    expect(wire).toEqual({
      strategy: "static",
      keys: [{ fields: ["zip"], transforms: [] }],
      ann_top_k: 50,
      learned_predicate_depth: 3,
    });
  });

  it("round-trips advanced strategies without losing fields", () => {
    const raw = {
      strategy: "learned",
      learned_sample_size: 5000,
      learned_min_recall: 0.95,
      learned_predicate_depth: 2,
    };
    const parsed = parseBlockingFromServer(raw);
    const back = serializeBlockingForWire(parsed);
    expect(back).toEqual(raw);
  });

  it("returns null when serializing null/undefined", () => {
    expect(serializeBlockingForWire(null)).toBeNull();
    expect(serializeBlockingForWire(undefined)).toBeNull();
  });
});
