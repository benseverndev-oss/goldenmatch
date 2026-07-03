/**
 * The wasm reroute is transparent: with `enableGoldencheckWasm()` the
 * CompositeKeyProfiler runs the shared goldencheck-core kernel; with the backend
 * disabled it runs the pure-TS BFS. Both must produce identical findings — that
 * equivalence is what makes the Rust core the source of truth (the pure-TS path
 * becomes a faithful fallback, not a divergent second implementation).
 */
import { describe, it, expect, afterEach } from "vitest";
import { TabularData } from "../../src/core/data.js";
import { CompositeKeyProfiler } from "../../src/core/relations/composite-key.js";
import {
  enableGoldencheckWasm,
  disableGoldencheckWasm,
} from "../../src/core/goldencheckWasm.js";

const profiler = new CompositeKeyProfiler();

function keysOf(data: TabularData): string[] {
  return profiler
    .profile(data)
    .map((f) => (f.metadata["key_columns"] as string[]).join("+"))
    .sort();
}

// A few tables that do / don't have composite keys.
function tables(): TabularData[] {
  return [
    // (order_id, line_no) is a composite key; neither unique alone.
    new TabularData([
      { order_id: 1, line_no: 1, sku: "a" },
      { order_id: 1, line_no: 2, sku: "b" },
      { order_id: 2, line_no: 1, sku: "a" },
      { order_id: 2, line_no: 2, sku: "c" },
      { order_id: 3, line_no: 1, sku: "d" },
    ]),
    // No composite key (every 2-subset still has dup tuples).
    new TabularData([
      { a: 1, b: 1 },
      { a: 1, b: 1 },
      { a: 2, b: 2 },
      { a: 2, b: 2 },
    ]),
    // Single-column key present -> profiler returns nothing regardless of path.
    new TabularData([
      { id: 1, grp: "x" },
      { id: 2, grp: "x" },
      { id: 3, grp: "y" },
    ]),
    // Three-way: (a,b) key, with nulls in the mix.
    new TabularData([
      { a: "p", b: "x", c: null },
      { a: "p", b: "y", c: "1" },
      { a: "q", b: "x", c: "1" },
      { a: "q", b: "y", c: null },
    ]),
  ];
}

describe("goldencheck wasm reroute — equivalence with pure-TS", () => {
  afterEach(() => disableGoldencheckWasm());

  it("wasm-backed composite keys == pure-TS on the same tables", () => {
    for (const data of tables()) {
      disableGoldencheckWasm();
      const pureTs = keysOf(data);
      enableGoldencheckWasm();
      const wasm = keysOf(data);
      expect(wasm).toEqual(pureTs);
    }
  });

  it("disable reverts to the pure-TS path", () => {
    const data = tables()[0]!;
    enableGoldencheckWasm();
    const on = keysOf(data);
    disableGoldencheckWasm();
    const off = keysOf(data);
    expect(on).toEqual(off);
    expect(off.length).toBeGreaterThan(0);
  });
});
