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

  it("covers jaro_winkler / levenshtein / token_sort / exact", () => {
    expect([...WASM_COVERED_SCORERS].sort()).toEqual(
      ["exact", "jaro_winkler", "levenshtein", "token_sort"],
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

  it("ignores the backend for an UNCOVERED scorer (soundex_match stays pure-TS)", () => {
    let called = false;
    setScorerBackend({
      scoreMatrix: (values) => {
        called = true;
        return new Float64Array(values.length * values.length);
      },
    });
    // soundex_match is NOT WASM-covered -> pure-TS scoreField (Robert/Rupert
    // share soundex R163 -> 1.0), so the backend stub must not be called.
    const m = scoreMatrix(["Robert", "Rupert"], "soundex_match");
    expect(called).toBe(false);
    expect(m[0]![1]).toBe(1.0);
  });

  it("zeros out null cells after a backend call", () => {
    setScorerBackend({
      scoreMatrix: (values) => new Float64Array(values.length * values.length).fill(1),
    });
    const m = scoreMatrix(["abc", null], "jaro_winkler");
    expect(m[0]![1]).toBe(0); // null cell masked to 0 despite backend returning 1
  });
});
