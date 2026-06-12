import { describe, it, expect, afterEach } from "vitest";
import {
  setScorerBackend,
  getScorerBackend,
  WASM_COVERED_SCORERS,
  type ScorerBackend,
} from "../../src/core/wasm/backend.js";
import { scoreMatrix } from "../../src/core/scorer.js";

const stub: ScorerBackend = {
  scoreMatrix: (values) => new Float64Array(values.length * values.length),
};

describe("ScorerBackend singleton", () => {
  afterEach(() => setScorerBackend(null));

  it("defaults to no backend", () => {
    expect(getScorerBackend()).toBeNull();
  });

  it("registers and clears a backend", () => {
    setScorerBackend(stub);
    expect(getScorerBackend()).toBe(stub);
    setScorerBackend(null);
    expect(getScorerBackend()).toBeNull();
  });

  it("covers exactly jaro_winkler / levenshtein / exact in slice 1", () => {
    expect([...WASM_COVERED_SCORERS].sort()).toEqual(
      ["exact", "jaro_winkler", "levenshtein"],
    );
  });
});

describe("scoreMatrix backend swap", () => {
  afterEach(() => setScorerBackend(null));

  it("routes a COVERED scorer through the backend", () => {
    const calls: string[] = [];
    setScorerBackend({
      scoreMatrix: (values, name) => {
        calls.push(name);
        return new Float64Array(values.length * values.length); // all zeros
      },
    });
    const m = scoreMatrix(["abc", "abd"], "jaro_winkler");
    expect(calls).toEqual(["jaro_winkler"]);
    expect(m[0]![1]).toBe(0); // came from the stub, not pure-TS (~0.9)
  });

  it("ignores the backend for an UNCOVERED scorer (token_sort stays pure-TS)", () => {
    let called = false;
    setScorerBackend({
      scoreMatrix: (values) => {
        called = true;
        return new Float64Array(values.length * values.length);
      },
    });
    const m = scoreMatrix(["a b", "b a"], "token_sort");
    expect(called).toBe(false);
    expect(m[0]![1]).toBeGreaterThan(0.99); // pure-TS token_sort ~1.0
  });

  it("zeros out null cells after a backend call", () => {
    setScorerBackend({
      scoreMatrix: (values) => new Float64Array(values.length * values.length).fill(1),
    });
    const m = scoreMatrix(["abc", null], "jaro_winkler");
    expect(m[0]![1]).toBe(0); // null cell masked to 0 despite backend returning 1
  });
});
