/**
 * sketch.ts — MinHash / LSH sketch kernel (parity port of `core/sketch.py`).
 *
 * Edge-safe: no Node.js imports. Uses only web-standard `TextEncoder` and
 * `DataView` (both available in browsers, Workers, Deno, and Node). This is the
 * pure-TypeScript reference port of `packages/python/goldenmatch/goldenmatch/
 * core/sketch.py` (and the Rust `goldenmatch-sketch-core` crate). All three
 * implementations produce **byte-identical** u64 output for identical inputs;
 * the committed golden vectors (`tests/fixtures/sketch_golden.json`) are the
 * contract. See `docs/superpowers/specs/2026-06-19-minhash-lsh-sketch-core-design.md`.
 *
 * PERF CAVEAT: all 64-bit arithmetic uses `BigInt` (masked to u64 after every
 * op) and the `mod (2^61-1)` permutation multiply needs the wide product, so
 * this is correctness-first, not speed-first. A WASM acceleration slice is
 * explicitly deferred (consistent with the `score-core` rollout); the pure-TS
 * path stays the default + fallback.
 */

// ---------------------------------------------------------------------------
// Constants (all u64 unless noted)
// ---------------------------------------------------------------------------

/** u64 wrapping mask. */
const MASK64 = 0xffffffffffffffffn;

const FNV_OFFSET = 0xcbf29ce484222325n;
const FNV_PRIME = 0x00000100000001b3n;
const SM_C1 = 0xbf58476d1ce4e5b9n;
const SM_C2 = 0x94d049bb133111ebn;
const SM_GAMMA = 0x9e3779b97f4a7c15n;
/** Mersenne prime 2^61 - 1, the permutation field modulus. */
const MERSENNE_P = (1n << 61n) - 1n;

/**
 * Exactly these six code points are word-mode separators (matches the Python
 * `_ASCII_WS` set and the Rust `is_ascii_ws`). NOT a language default
 * whitespace splitter (`/\s/`, `String.prototype.split` with no arg, etc.):
 * those include Unicode whitespace (U+00A0, the `Zs` category, ZWSP, ...) and
 * one disagreement on a separator changes the token set and breaks parity.
 */
const ASCII_WS = new Set<number>([0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x20]);

const ENCODER = new TextEncoder();

// ---------------------------------------------------------------------------
// base_hash + splitmix64
// ---------------------------------------------------------------------------

/**
 * FNV-1a (64-bit) over `data`, then a splitmix64 finalizer for avalanche.
 * `data` is a byte view (UTF-8 bytes for strings). Returns a u64 as a bigint.
 */
export function baseHash(data: Uint8Array): bigint {
  let h = FNV_OFFSET;
  for (let i = 0; i < data.length; i++) {
    h = ((h ^ BigInt(data[i]!)) * FNV_PRIME) & MASK64;
  }
  h = ((h ^ (h >> 30n)) * SM_C1) & MASK64;
  h = ((h ^ (h >> 27n)) * SM_C2) & MASK64;
  return (h ^ (h >> 31n)) & MASK64;
}

/** UTF-8-encode `s` and `baseHash` it. */
function baseHashStr(s: string): bigint {
  return baseHash(ENCODER.encode(s));
}

/**
 * One splitmix64 step. Returns `[value, newState]`.
 *
 * The increment is applied *before* finalization, so a stream seeded at `S`
 * produces its first value as `finalize(S + SM_GAMMA)` — there is no raw-seed
 * draw. (Do NOT substitute a stdlib/reference splitmix64 that increments after
 * producing a value; that variant silently breaks parity.)
 */
export function splitmix64(state: bigint): [bigint, bigint] {
  state = (state + SM_GAMMA) & MASK64;
  let z = state;
  z = ((z ^ (z >> 30n)) * SM_C1) & MASK64;
  z = ((z ^ (z >> 27n)) * SM_C2) & MASK64;
  z = (z ^ (z >> 31n)) & MASK64;
  return [z, state];
}

// ---------------------------------------------------------------------------
// shingle
// ---------------------------------------------------------------------------

/** Split `text` into maximal runs separated by the ASCII whitespace set. */
function wordTokens(text: string): string[] {
  const out: string[] = [];
  let cur = "";
  for (const ch of text) {
    // ch is a single code point (string iteration is code-point-based).
    if (ASCII_WS.has(ch.codePointAt(0)!)) {
      if (cur.length > 0) {
        out.push(cur);
        cur = "";
      }
    } else {
      cur += ch;
    }
  }
  if (cur.length > 0) out.push(cur);
  return out;
}

/**
 * Return the sorted, deduplicated set of shingle hashes for `text`.
 *
 * `mode="char"` windows over Unicode code points (`Array.from`, NOT UTF-16
 * units); `mode="word"` windows over tokens split on the ASCII whitespace set.
 * `n === 0` (empty or, in word mode, whitespace-only) yields the empty set and
 * takes precedence over the short-input branch; `1 <= n < k` yields a single
 * whole-sequence shingle. `k` must be `>= 1` (throws otherwise — every port
 * rejects `k < 1` identically so Rust `windows(0)` can't diverge).
 *
 * Output is sorted **numerically as BigInt** (not lexically) and deduped.
 */
export function shingle(text: string, mode: string, k: number): bigint[] {
  if (k < 1) {
    throw new Error(`shingle k must be >= 1, got ${k}`);
  }

  let units: string[];
  let sep: string;
  if (mode === "char") {
    units = Array.from(text); // code points, not UTF-16 units
    sep = "";
  } else if (mode === "word") {
    units = wordTokens(text);
    sep = " ";
  } else {
    throw new Error(`unknown shingle mode: ${JSON.stringify(mode)}`);
  }

  const n = units.length;
  if (n === 0) return [];

  // Dedup via a Set of string keys (BigInt is not a structural Set key), then
  // sort numerically.
  const seen = new Set<bigint>();
  if (n < k) {
    seen.add(baseHashStr(units.join(sep)));
  } else {
    for (let i = 0; i + k <= n; i++) {
      seen.add(baseHashStr(units.slice(i, i + k).join(sep)));
    }
  }
  const out = Array.from(seen);
  out.sort((a, b) => (a < b ? -1 : a > b ? 1 : 0)); // numeric BigInt sort
  return out;
}

// ---------------------------------------------------------------------------
// signature + estimate_jaccard
// ---------------------------------------------------------------------------

/**
 * Derive the `(a, b)` permutation coefficients from `seed` via a splitmix64
 * stream. `a[i]` in `[1, P-1]`, `b[i]` in `[0, P-1]`. Coefficients may repeat —
 * do not deduplicate.
 */
function coefficients(numPerms: number, seed: bigint): [bigint[], bigint[]] {
  const a: bigint[] = [];
  const b: bigint[] = [];
  let state = seed & MASK64;
  for (let i = 0; i < numPerms; i++) {
    let v: bigint;
    [v, state] = splitmix64(state);
    a.push((v % (MERSENNE_P - 1n)) + 1n);
    [v, state] = splitmix64(state);
    b.push(v % MERSENNE_P);
  }
  return [a, b];
}

/**
 * MinHash signature of a shingle set. Empty set => all `0xFFFFFFFFFFFFFFFFn`
 * (u64::MAX). The `(a*x + b) mod p` product is computed with BigInt (the wide
 * intermediate the Rust port does in u128).
 */
export function signature(
  shingles: readonly bigint[],
  numPerms: number,
  seed: bigint,
): bigint[] {
  const [a, b] = coefficients(numPerms, seed);
  const sig: bigint[] = new Array<bigint>(numPerms).fill(MASK64);
  for (let i = 0; i < numPerms; i++) {
    const ai = a[i]!;
    const bi = b[i]!;
    let m = MASK64;
    for (const x of shingles) {
      const xr = x % MERSENNE_P;
      const p = (ai * xr + bi) % MERSENNE_P;
      if (p < m) m = p;
    }
    sig[i] = m;
  }
  return sig;
}

/** Estimated Jaccard similarity = fraction of equal signature positions. */
export function estimateJaccard(
  sigA: readonly bigint[],
  sigB: readonly bigint[],
): number {
  if (sigA.length === 0) return 0.0;
  let eq = 0;
  const n = Math.min(sigA.length, sigB.length);
  for (let i = 0; i < n; i++) {
    if (sigA[i] === sigB[i]) eq++;
  }
  return eq / sigA.length;
}

// ---------------------------------------------------------------------------
// band_hashes
// ---------------------------------------------------------------------------

/**
 * Banded-LSH bucket id per band over little-endian signature bytes.
 * `sig.length` must be divisible by `numBands` (throws otherwise). Each band's
 * buffer is `8*(r+1)` bytes: 8 LE bytes of `band_idx` (as u64) followed by 8 LE
 * bytes of each of its `r` signature values. Mixing `band_idx` in prevents
 * identical row-tuples in different bands from colliding into one bucket space.
 */
export function bandHashes(sig: readonly bigint[], numBands: number): bigint[] {
  const n = sig.length;
  if (numBands <= 0 || n % numBands !== 0) {
    throw new Error(`num_perms ${n} not divisible by num_bands ${numBands}`);
  }
  const r = n / numBands;
  const out: bigint[] = [];
  for (let band = 0; band < numBands; band++) {
    const buf = new Uint8Array(8 * (r + 1));
    const view = new DataView(buf.buffer);
    view.setBigUint64(0, BigInt(band) & MASK64, true); // true = little-endian
    for (let j = 0; j < r; j++) {
      view.setBigUint64(8 * (j + 1), sig[band * r + j]! & MASK64, true);
    }
    out.push(baseHash(buf));
  }
  return out;
}

// ---------------------------------------------------------------------------
// optimal_bands (host-side helper, NOT on the byte-exact hash path)
// ---------------------------------------------------------------------------

/**
 * Pick `[numBands, rowsPerBand]` whose LSH S-curve best matches `threshold`.
 *
 * Host-side configuration helper only — its result is fed to `bandHashes` as an
 * explicit `numBands`; it never enters the hash path, so floats are fine here.
 * Deterministic: an ascending divisor scan, a fixed 1000-step trapezoidal
 * integral of the S-curve `1 - (1 - s^r)^b`, and a strict-improvement tie-break
 * (`err < best - 1e-12`) that keeps the smaller `numBands` found first.
 */
export function optimalBands(
  numPerms: number,
  threshold: number,
  steps = 1000,
): [number, number] {
  // Collision probability for the (b, r) S-curve.
  const pc = (s: number, r: number, b: number): number =>
    1.0 - Math.pow(1.0 - Math.pow(s, r), b);

  const integral = (lo: number, hi: number, f: (s: number) => number): number => {
    const h = (hi - lo) / steps;
    let s = 0.5 * (f(lo) + f(hi));
    for (let i = 1; i < steps; i++) {
      s += f(lo + i * h);
    }
    return s * h;
  };

  let best: [number, number, number] | null = null;
  for (let b = 1; b <= numPerms; b++) {
    if (numPerms % b !== 0) continue;
    const r = numPerms / b;
    const fp = integral(0.0, threshold, (s) => pc(s, r, b));
    const fn = integral(threshold, 1.0, (s) => 1.0 - pc(s, r, b));
    const err = 0.5 * fp + 0.5 * fn;
    if (best === null || err < best[2] - 1e-12) {
      best = [b, r, err];
    }
  }
  // numPerms >= 1 always yields b=1, so best is never null in practice.
  if (best === null) {
    throw new Error(`optimal_bands: num_perms must be >= 1, got ${numPerms}`);
  }
  return [best[0], best[1]];
}

// ---------------------------------------------------------------------------
// sketch_band_hashes (end-to-end compose)
// ---------------------------------------------------------------------------

/**
 * End-to-end: `text` -> shingle -> signature -> band hashes. The single entry
 * point a blocker calls per record.
 */
export function sketchBandHashes(
  text: string,
  mode: string,
  k: number,
  numPerms: number,
  numBands: number,
  seed: bigint,
): bigint[] {
  return bandHashes(signature(shingle(text, mode, k), numPerms, seed), numBands);
}
