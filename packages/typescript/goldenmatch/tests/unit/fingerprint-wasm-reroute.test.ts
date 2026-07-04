/**
 * Reroute equivalence for `recordFingerprint`: with the fingerprint wasm backend
 * enabled, a JSON-primitive-safe record is hashed via the shared fingerprint-core
 * kernel; disabled, via the pure-TS canonicalizer. Both MUST produce the SAME
 * digest — which makes the Rust core the source of truth (pure-TS = faithful
 * fallback) and closes the divergence risk of the hand-written canonicalizer.
 *
 * Records the wasm path can't take (bigint / Uint8Array) MUST transparently fall
 * back to pure-TS even while the backend is enabled — same digest either way.
 */
import { describe, it, expect, afterEach } from "vitest";
import { recordFingerprint } from "../../src/core/record-fingerprint.js";
import {
  enableFingerprintWasm,
  disableFingerprintWasm,
} from "../../src/core/fingerprintWasm.js";

// JSON-primitive-safe records that must go through the wasm kernel.
const SAFE: Record<string, unknown>[] = [
  {},
  { a: "x" },
  { a: 1 },
  { n: 1.5 },
  { ok: true, off: false },
  { a: null },
  { name: "Acme", city: "NYC", zip: "10001" },
  { a: 1, b: "1" }, // type-tag: int 1 != str "1"
  { id: 7, active: true, score: 98.6, label: "gold", note: null },
  { a: 1, __row_id__: 9, __source__: "df" }, // __-keys dropped by both
];

// Records with values JSON can't carry — must fall back to pure-TS.
const FALLBACK: Record<string, unknown>[] = [
  { big: 12345678901234567890n }, // bigint
  { blob: new Uint8Array([1, 2, 3, 255]) }, // Uint8Array
  { big: 9007199254740993n, name: "x" }, // 2^53+1, exact only as bigint
];

describe("fingerprint wasm reroute — recordFingerprint equivalence", () => {
  afterEach(() => disableFingerprintWasm());

  it("JSON-safe records: wasm == pure-TS", async () => {
    for (const rec of SAFE) {
      disableFingerprintWasm();
      const pureTs = await recordFingerprint(rec);
      enableFingerprintWasm();
      const wasm = await recordFingerprint(rec);
      expect(wasm, `record ${JSON.stringify(rec)}`).toBe(pureTs);
      expect(wasm).toMatch(/^[0-9a-f]{64}$/);
    }
  });

  it("bigint / Uint8Array records fall back to pure-TS (same digest either way)", async () => {
    for (const rec of FALLBACK) {
      disableFingerprintWasm();
      const off = await recordFingerprint(rec);
      enableFingerprintWasm();
      const on = await recordFingerprint(rec);
      expect(on, `record ${String(Object.keys(rec))}`).toBe(off);
    }
  });

  it("the __-key drop matches: {a:1,__x__:9} == {a:1}", async () => {
    enableFingerprintWasm();
    expect(await recordFingerprint({ a: 1, __x__: 9 })).toBe(
      await recordFingerprint({ a: 1 }),
    );
  });
});
