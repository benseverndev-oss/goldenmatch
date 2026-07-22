import { describe, it, expect, afterEach } from "vitest";
import {
  setScorerBackend,
  getScorerBackend,
  WASM_COVERED_SCORERS,
  type ScorerBackend,
} from "../../src/core/wasm/backend.js";
import { scoreMatrix, setSyncEmbedder } from "../../src/core/scorer.js";

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

  it("covers the 14 score_one scorers + the 2 fs-core name scorers", () => {
    expect([...WASM_COVERED_SCORERS].sort()).toEqual(
      [
        "audio_fp",
        "date",
        "dice",
        "ensemble",
        "exact",
        "given_name_aliased_jw",
        "initialism_match",
        "jaccard",
        "jaro_winkler",
        "levenshtein",
        "name_freq_weighted_jw",
        "phash",
        "qgram",
        "radial",
        "soundex_match",
        "token_sort",
      ],
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

  it("routes soundex_match through the backend now that it's covered", () => {
    const calls: string[] = [];
    setScorerBackend({
      scoreMatrix: (values, name) => {
        calls.push(name);
        return new Float64Array(values.length * values.length); // all zeros
      },
    });
    // soundex_match (score_one id 6) is WASM-covered -> routes to the backend.
    // Robert/Rupert share soundex R163 (pure-TS would be 1.0); the stub returns
    // 0, proving the WASM path (not pure-TS) ran.
    const m = scoreMatrix(["Robert", "Rupert"], "soundex_match");
    expect(calls).toEqual(["soundex_match"]);
    expect(m[0]![1]).toBe(0);
  });

  it("ignores the backend for an UNCOVERED scorer (embedding stays pure-TS)", () => {
    let called = false;
    setScorerBackend({
      scoreMatrix: (values) => {
        called = true;
        return new Float64Array(values.length * values.length).fill(0.5);
      },
    });
    // embedding is NOT WASM-covered -> pure-TS scoreField (a deterministic stub
    // embedder makes it a real cosine), so the stub's 0.5 must not appear and
    // the backend must not be called.
    setSyncEmbedder((s) => [s.length, s.charCodeAt(0) || 0]);
    try {
      const m = scoreMatrix(["abc", "abd"], "embedding");
      expect(called).toBe(false);
      expect(m[0]![1]).not.toBe(0.5);
    } finally {
      setSyncEmbedder(null);
    }
  });

  it("zeros out null cells after a backend call", () => {
    setScorerBackend({
      scoreMatrix: (values) => new Float64Array(values.length * values.length).fill(1),
    });
    const m = scoreMatrix(["abc", null], "jaro_winkler");
    expect(m[0]![1]).toBe(0); // null cell masked to 0 despite backend returning 1
  });
});
