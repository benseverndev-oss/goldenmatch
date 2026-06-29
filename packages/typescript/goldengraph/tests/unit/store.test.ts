/**
 * Bitemporal store: append (fresh + chained), as-of slice, history, and the
 * unregistered-throw contract.
 */
import { describe, it, expect, afterEach } from "vitest";
import { appendBatch, asOf, disableGoldengraphWasm, type StoreBatch } from "../../src/index.js";
import { enableGoldengraphWasm } from "../../src/core/goldengraphWasm.js";

const batch1: StoreBatch = {
  entities: [
    { local_id: 0, canonical_name: "Apple Inc", typ: "Company", surface_names: ["Apple Inc"], record_keys: ["k_apple"] },
    { local_id: 1, canonical_name: "Tim Cook", typ: "Person", surface_names: ["Tim Cook"], record_keys: ["k_tim"] },
  ],
  edges: [{ subj_local: 1, predicate: "ceo_of", obj_local: 0, valid_from: 100, valid_to: null, source_refs: ["doc1"] }],
  ingested_at: 100,
};
const batch2: StoreBatch = {
  entities: [{ local_id: 0, canonical_name: "Apple", typ: "Company", surface_names: ["Apple"], record_keys: ["k_apple"] }],
  edges: [],
  ingested_at: 200,
};

describe("goldengraph bitemporal store", () => {
  afterEach(() => {
    disableGoldengraphWasm();
  });

  it("throws when wasm is not enabled", () => {
    disableGoldengraphWasm();
    expect(() => appendBatch(null, batch1)).toThrowError(/requires the wasm backend/i);
  });

  it("append (fresh) opens a store with the batch's entities", () => {
    enableGoldengraphWasm();
    const snap = appendBatch(null, batch1);
    expect(Object.keys(snap.entities).length).toBe(2);
    expect(snap.next_id).toBeGreaterThan(0);
  });

  it("chained append + as-of dedups by record_key (no duplicate entity)", () => {
    enableGoldengraphWasm();
    let snap = appendBatch(null, batch1);
    snap = appendBatch(snap, batch2); // same k_apple -> updates entity 0, no new entity

    const graph = asOf(snap, 250, 250);
    // Still 2 entities: batch2's k_apple matched entity 0 (no duplicate added).
    expect(graph.entities.length).toBe(2);
    // The matched entity is updated to the latest batch (canonical "Apple",
    // surfaces ["Apple"]) — the kernel is latest-wins, not a surface union.
    const apple = graph.entities.find((e) => e.canonical_name === "Apple");
    expect(apple).toBeDefined();
    expect(apple?.surface_names).toEqual(["Apple"]);
    // Tim Cook + the CEO edge survive.
    expect(graph.entities.some((e) => e.canonical_name === "Tim Cook")).toBe(true);
    expect(graph.edges.some((e) => e.predicate === "ceo_of")).toBe(true);
  });
});
