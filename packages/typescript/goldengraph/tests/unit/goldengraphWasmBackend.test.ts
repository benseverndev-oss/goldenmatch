/**
 * Backend registry + the unregistered-throw contract. The query functions must
 * REFUSE (throw an actionable error) when the wasm backend isn't enabled.
 */
import { describe, it, expect, afterEach } from "vitest";
import {
  buildGraph,
  communities,
  isGoldengraphWasmEnabled,
  disableGoldengraphWasm,
} from "../../src/index.js";
import { enableGoldengraphWasm } from "../../src/core/goldengraphWasm.js";

const mentions = [
  { name: "Apple Inc", typ: "Company" },
  { name: "Apple", typ: "Company" },
];
const edges: { subj: number; predicate: string; obj: number; source_ref: string }[] = [];
const resolution = { 0: 0, 1: 0 };

describe("goldengraph wasm backend registry", () => {
  afterEach(() => {
    disableGoldengraphWasm();
  });

  it("throws when wasm is not enabled", () => {
    disableGoldengraphWasm();
    expect(isGoldengraphWasmEnabled()).toBe(false);
    expect(() => buildGraph(mentions, edges, resolution)).toThrowError(/requires the wasm backend/i);
  });

  it("builds + queries once enabled", () => {
    enableGoldengraphWasm();
    expect(isGoldengraphWasmEnabled()).toBe(true);
    const graph = buildGraph(mentions, edges, resolution);
    expect(graph.entities.length).toBe(1);
    const comms = communities(graph);
    expect(comms).toEqual([{ id: 0, members: [0] }]);
  });

  it("disable restores the refusing state", () => {
    enableGoldengraphWasm();
    disableGoldengraphWasm();
    expect(isGoldengraphWasmEnabled()).toBe(false);
    expect(() => buildGraph(mentions, edges, resolution)).toThrowError(/requires the wasm backend/i);
  });
});
