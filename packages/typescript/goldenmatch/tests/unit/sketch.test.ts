/**
 * sketch.test.ts — cross-language parity for the MinHash/LSH sketch kernel.
 *
 * Replays the committed golden vectors (generated from the Python reference
 * `core/sketch.py`) through the TS port and asserts byte-identical output. All
 * u64 values are compared as DECIMAL STRINGS (`String(bigint)`) to dodge any
 * Number precision loss. Also pins the headline golden constants directly so a
 * regression is caught even if the fixture is ever regenerated wrong.
 */
import { describe, it, expect } from "vitest";
import {
  baseHash,
  splitmix64,
  shingle,
  signature,
  bandHashes,
  optimalBands,
  sketchBandHashes,
  estimateJaccard,
} from "../../src/core/sketch.js";
import { MinHashLSHBlocker } from "../../src/core/lshBlocker.js";
import goldenCases from "../fixtures/sketch_golden.json" with { type: "json" };

interface GoldenCase {
  text: string;
  mode: string;
  k: number;
  num_perms: number;
  num_bands: number;
  seed: number;
  shingles: string[];
  signature: string[];
  band_hashes: string[];
}

const cases = goldenCases as unknown as GoldenCase[];

/** Render a bigint array as decimal strings for precision-safe comparison. */
function asDecimals(arr: readonly bigint[]): string[] {
  return arr.map((v) => String(v));
}

describe("sketch golden vectors (parity with core/sketch.py)", () => {
  for (const c of cases) {
    it(`${JSON.stringify(c.text).slice(0, 32)} [${c.mode} k=${c.k} perms=${c.num_perms} bands=${c.num_bands} seed=${c.seed}]`, () => {
      const sh = shingle(c.text, c.mode, c.k);
      expect(asDecimals(sh)).toEqual(c.shingles);

      const sig = signature(sh, c.num_perms, BigInt(c.seed));
      expect(asDecimals(sig)).toEqual(c.signature);

      const bands = bandHashes(sig, c.num_bands);
      expect(asDecimals(bands)).toEqual(c.band_hashes);

      // End-to-end compose must equal the per-stage result.
      const e2e = sketchBandHashes(
        c.text,
        c.mode,
        c.k,
        c.num_perms,
        c.num_bands,
        BigInt(c.seed),
      );
      expect(asDecimals(e2e)).toEqual(c.band_hashes);
    });
  }
});

describe("sketch headline constants", () => {
  it("baseHash(\"\") === 17665956581633026203n", () => {
    expect(baseHash(new TextEncoder().encode(""))).toBe(17665956581633026203n);
  });

  it("baseHash of a few known byte strings", () => {
    const enc = (s: string) => new TextEncoder().encode(s);
    expect(baseHash(enc("a"))).toBe(198367012849983736n);
    expect(baseHash(enc("ab"))).toBe(11528740771484442951n);
    expect(baseHash(enc("hello world"))).toBe(417524495691944273n);
  });

  it("splitmix64 stream from 0: first four draws", () => {
    let state = 0n;
    const out: bigint[] = [];
    for (let i = 0; i < 4; i++) {
      const [v, s] = splitmix64(state);
      out.push(v);
      state = s;
    }
    expect(out).toEqual([
      16294208416658607535n,
      7960286522194355700n,
      487617019471545679n,
      17909611376780542444n,
    ]);
  });

  it("optimalBands(128, 0.5) deep-equals [32, 4]", () => {
    expect(optimalBands(128, 0.5)).toEqual([32, 4]);
  });

  it("optimalBands matches the Rust goldens for other thresholds", () => {
    expect(optimalBands(128, 0.8)).toEqual([8, 16]);
    expect(optimalBands(128, 0.9)).toEqual([4, 32]);
  });
});

describe("sketch edge cases", () => {
  it("k < 1 throws", () => {
    expect(() => shingle("hello", "char", 0)).toThrow();
  });

  it("unknown mode throws", () => {
    expect(() => shingle("hello", "bigram", 3)).toThrow();
  });

  it("empty text yields empty shingle set", () => {
    expect(shingle("", "char", 3)).toEqual([]);
  });

  it("whitespace-only word mode yields empty set (precedence over short-input)", () => {
    expect(shingle("   \t\n", "word", 2)).toEqual([]);
  });

  it("empty shingles => signature is all u64::MAX", () => {
    const sig = signature([], 8, 0n);
    expect(sig).toEqual(new Array<bigint>(8).fill((1n << 64n) - 1n));
  });

  it("bandHashes throws on non-divisible signature length", () => {
    expect(() => bandHashes(new Array<bigint>(8).fill(0n), 3)).toThrow();
  });

  it("estimateJaccard of a signature with itself is 1.0", () => {
    const sig = signature(shingle("the quick brown fox jumps", "word", 2), 128, 7n);
    expect(estimateJaccard(sig, sig)).toBe(1.0);
  });

  it("word mode splits only on the 6 ASCII whitespace code points", () => {
    // U+00A0 (non-breaking space) is NOT a separator -> one token.
    const NBSP = String.fromCharCode(0x00a0);
    expect(shingle(`a${NBSP}b`, "word", 1).length).toBe(1);
    // The 6 ASCII whitespace code points {tab, LF, VT, FF, CR, space} ARE
    // separators -> two tokens. Built from char codes so no literal control
    // bytes live in this source file.
    for (const code of [0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x20]) {
      const sep = String.fromCharCode(code);
      expect(shingle(`a${sep}b`, "word", 1).length).toBe(2);
    }
  });
});

describe("MinHashLSHBlocker", () => {
  it("recovers near-duplicate texts as candidate pairs; empties excluded", () => {
    // Two near-identical sentences (one token swapped) + an unrelated one + two
    // empties. Char-shingling at k=4 with many perms should bucket the near-dups
    // together while leaving the unrelated text and the empties out.
    const texts = [
      "the quick brown fox jumps over the lazy dog",
      "the quick brown fox jumps over the lazy dawg",
      "completely unrelated content about astrophysics",
      "",
      "   \t",
    ];
    const blocker = MinHashLSHBlocker.fromConfig({
      mode: "char",
      k: 4,
      numPerms: 64,
      threshold: 0.6,
      seed: 0n,
    });

    const pairs = blocker.candidatePairs(texts);
    // The two near-duplicates (rows 0 and 1) must collide.
    expect(pairs.has("0,1")).toBe(true);

    // The empties (rows 3, 4) share no bucket with anything.
    for (const key of pairs) {
      expect(key).not.toContain("3");
      expect(key).not.toContain("4");
    }

    // The unrelated text (row 2) should not match the near-dup cluster.
    expect(pairs.has("0,2")).toBe(false);
    expect(pairs.has("1,2")).toBe(false);
  });

  it("buckets skip the empty sentinel rows", () => {
    // Word mode: "" and a whitespace-only string both tokenize to zero tokens
    // (the all-u64::MAX sentinel) and are dropped; only row 0 has content.
    // (NB: in CHAR mode "   " is three real code points, not empty — the
    // sentinel only catches a truly empty unit sequence.)
    const blocker = MinHashLSHBlocker.fromConfig({
      mode: "word",
      k: 2,
      numPerms: 16,
      numBands: 4,
      seed: 0n,
    });
    const buckets = blocker.buckets(["hello there world", "", "   \t"]);
    // Every grouped row index must be 0 (the only non-empty row).
    for (const members of buckets.values()) {
      for (const m of members) expect(m).toBe(0);
    }
  });

  it("fromConfig resolves numBands from threshold via optimalBands", () => {
    const blocker = MinHashLSHBlocker.fromConfig({
      mode: "char",
      k: 3,
      numPerms: 128,
      threshold: 0.5,
      seed: 0n,
    });
    expect(blocker.numBands).toBe(32);
  });
});
