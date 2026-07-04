/**
 * simhash.ts — SimHash / LSH sketch kernel (parity port of `core/sketch.py`'s
 * SimHash functions, #1082).
 *
 * The *semantic* near-duplicate sketch: project a dense embedding through random
 * hyperplanes (a Rademacher ±1 matrix drawn from a splitmix64 bitstream), take
 * the sign of each dot product as a 0/1 signature bit, then band the signature
 * into LSH buckets. Cosine-near vectors agree on most signs, so they collide in
 * a band. Complements the lexical MinHash sketch in `sketch.ts`.
 *
 * Edge-safe: no Node.js imports. Reuses `baseHash` (FNV-1a + splitmix finalizer)
 * and `splitmix64` from `sketch.ts` — the projection bitstream and the band
 * hash are byte-identical to the Python reference and the Rust kernel. The
 * committed golden vectors (`tests/fixtures/sketch_simhash_golden.json`,
 * generated from `core/sketch.py`) are the contract.
 *
 * PERF CAVEAT: the bitstream uses `BigInt` (the splitmix64 state is u64); the
 * dot products are plain f64 `number`. Correctness-first, consistent with the
 * `sketch.ts` MinHash port.
 */

import { baseHash, splitmix64 } from "./sketch.js";
import { getSketchWasmBackend } from "./sketchWasmBackend.js";

/** u64 wrapping mask (only used for the LE band-index write). */
const MASK64 = 0xffffffffffffffffn;

// ---------------------------------------------------------------------------
// projection matrix
// ---------------------------------------------------------------------------

/**
 * Row-major `numPlanes x dim` Rademacher (±1) matrix from a splitmix64 bitstream
 * seeded at `seed`.
 *
 * One bit per entry, LSB first, refilling a 64-bit buffer from the stream. Draw
 * order is plane 0 col 0..dim, plane 1 col 0..dim, ... — the Python reference
 * and Rust kernel draw in the same order, so the matrix is byte-identical.
 *
 * `state`/`buf` stay BigInt (the splitmix64 contract); each matrix entry is a
 * plain `number` (+1 / -1) so the dot product below is f64.
 */
function projectionMatrix(
  numPlanes: number,
  dim: number,
  seed: bigint,
): number[][] {
  let state = seed & MASK64;
  let buf = 0n;
  let bitsLeft = 0;
  const planes: number[][] = [];
  for (let p = 0; p < numPlanes; p++) {
    const row: number[] = new Array<number>(dim);
    for (let j = 0; j < dim; j++) {
      if (bitsLeft === 0) {
        [buf, state] = splitmix64(state);
        bitsLeft = 64;
      }
      row[j] = (buf & 1n) === 1n ? 1 : -1;
      buf >>= 1n;
      bitsLeft -= 1;
    }
    planes.push(row);
  }
  return planes;
}

// ---------------------------------------------------------------------------
// simhashSignature
// ---------------------------------------------------------------------------

/**
 * SimHash signature: one bit (0/1) per random hyperplane.
 *
 * `sig[i] = 1` iff the dot product of plane `i` with `vector` is `>= 0.0` (tie,
 * including the all-zero vector where every dot is exactly 0.0, resolves to 1).
 * All float math is f64; the dot sums `j` ascending.
 */
export function simhashSignature(
  vector: readonly number[],
  numPlanes: number,
  seed: bigint,
): number[] {
  // Rust-source-of-truth: the shared sketch-core kernel (same projection
  // bitstream the Python native path + the SQL surfaces run) when the sketch
  // wasm backend is enabled; the pure-TS BigInt projection below stays the
  // faithful fallback. Byte-identical (golden-verified), so the signature is
  // unchanged whether or not the backend is enabled.
  const backend = getSketchWasmBackend();
  if (backend) {
    return backend.simhashSignature(vector, numPlanes, seed);
  }

  const dim = vector.length;
  const planes = projectionMatrix(numPlanes, dim, seed);
  const sig: number[] = new Array<number>(numPlanes);
  for (let i = 0; i < numPlanes; i++) {
    const row = planes[i]!;
    let dot = 0.0;
    for (let j = 0; j < dim; j++) {
      dot += row[j]! * vector[j]!;
    }
    sig[i] = dot >= 0.0 ? 1 : 0;
  }
  return sig;
}

// ---------------------------------------------------------------------------
// simhashBandHashes
// ---------------------------------------------------------------------------

/**
 * Banded LSH over the 0/1 SimHash signature bytes.
 *
 * `sig.length` must be divisible by `numBands` (throws otherwise). For band `b`
 * the bucket id is `baseHash(le8(b) ++ bytes(sig[b*r:(b+1)*r]))` — the band index
 * as 8 little-endian bytes, then one byte (0 or 1) per plane-bit in the band.
 * Mirrors the MinHash `bandHashes` byte layout (u64 band-index prefix + per-
 * element bytes).
 */
export function simhashBandHashes(
  sig: readonly number[],
  numBands: number,
): bigint[] {
  const n = sig.length;
  if (numBands <= 0 || n % numBands !== 0) {
    throw new Error(`num_planes ${n} not divisible by num_bands ${numBands}`);
  }
  const r = n / numBands;
  const out: bigint[] = [];
  for (let band = 0; band < numBands; band++) {
    const buf = new Uint8Array(8 + r);
    // 8 LE bytes of the band index (as u64), then one byte per signature bit.
    new DataView(buf.buffer).setBigUint64(0, BigInt(band) & MASK64, true);
    for (let t = 0; t < r; t++) {
      buf[8 + t] = sig[band * r + t]!; // 0 or 1
    }
    out.push(baseHash(buf));
  }
  return out;
}
