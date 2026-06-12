import { describe, it, expect, afterEach } from "vitest";
import {
  setScorerBackend,
  getScorerBackend,
  WASM_COVERED_SCORERS,
  type ScorerBackend,
} from "../../src/core/wasm/backend.js";

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
