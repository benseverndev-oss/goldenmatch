/**
 * perceptualWasm.ts — the OPT-IN perceptual image-pHash surface
 * (`goldenmatch/core/perceptual-wasm`).
 *
 * Computes a 64-bit DCT perceptual hash of a decoded luma grid, **bit-identical**
 * to the Python reference and the native kernel (the DCT basis is a committed
 * constant table — no runtime libm divergence), so a hash computed here in
 * JS/Workers/edge can be compared directly against a Python-built index.
 *
 *   import { phashImage, hamming } from "goldenmatch/core/perceptual-wasm";
 *   const h = phashImage(lumaGrid);          // "0x..." 64-bit hex
 *   const d = hamming(h, otherHash);          // bit distance (0 = identical)
 *
 * Edge-safe: no `node:*` imports; the wasm is inlined as base64 and instantiated
 * synchronously via `initSync`, so the API stays sync. The base `goldenmatch`
 * entry carries NONE of this — only importing this subpath pulls the wasm bytes.
 *
 * Scope: image pHash only. The radial-variance profile and audio fingerprint are
 * Python/native-only (their non-cos float steps aren't cross-platform stable).
 */
import {
  initSync,
  phash_image_hex,
  hamming_hex,
} from "./_wasm/perceptualWasmBindings.js";
import { PERCEPTUAL_WASM_BASE64 } from "./_wasm/perceptualWasmBytes.js";

let _inited = false;

function base64ToBytes(b64: string): Uint8Array {
  // atob is available in browsers, Workers, and Node >= 18 — edge-safe.
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

/** Initialize the wasm module (idempotent). Called lazily by the API below. */
export function enablePerceptualWasm(): void {
  if (_inited) return;
  initSync({ module: base64ToBytes(PERCEPTUAL_WASM_BASE64) });
  _inited = true;
}

/**
 * 64-bit DCT perceptual hash of a decoded luma grid (rows of grayscale values),
 * returned as a `0x`-prefixed 16-hex-digit string. Bit-identical to
 * `goldenmatch.core.perceptual.phash_image`. Throws on an empty grid.
 */
export function phashImage(grid: readonly (readonly number[])[]): string {
  enablePerceptualWasm();
  return phash_image_hex(JSON.stringify(grid));
}

/** Bit (hamming) distance between two 64-bit pHash hex strings (0 = identical). */
export function hamming(aHex: string, bHex: string): number {
  enablePerceptualWasm();
  return hamming_hex(aHex, bHex);
}
