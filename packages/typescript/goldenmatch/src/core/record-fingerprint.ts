/**
 * Canonical record fingerprint — the cross-surface stable record-id hash.
 *
 * Byte-parity with the Python reference
 * (`goldenmatch.core._hashing.record_fingerprint`), the native C ABI
 * (`gm_record_fingerprint`), and the DuckDB / Postgres
 * `goldenmatch_record_fingerprint` SQL functions. Spec:
 * `docs/design/2026-05-26-stable-record-hash-cabi-plan.md`.
 *
 * Edge-safe: uses the global Web Crypto API (`crypto.subtle.digest`); MUST NOT
 * import `node:*`. SHA-256 is async, so this returns a Promise.
 *
 * Canonicalization v1: drop `__`-prefixed fields; sort by name; for each append
 * `name 0x1f TAG value 0x1e`; type-tagged values (so int 1 != str "1" != true):
 *   null -> 'n' | bool -> 'b' + '1'/'0' | int -> 'i' + decimal |
 *   float -> 'f' + 16 hex of IEEE-754 big-endian bits (-0 -> 0; NaN/Inf reject) |
 *   string -> 's' + UTF-8 | bytes (Uint8Array) -> 'y' + raw.
 *
 * JS-number caveats (documented divergences from Python's int/float type model):
 *   - A whole-number `number` is treated as an int, so `1.0` hashes like Python
 *     int `1`, NOT Python float `1.0`. Pass a non-integer for float semantics.
 *   - `number` integers above 2^53 lose precision; use `bigint` for exact large
 *     ints (bigint -> int tag).
 *   - Field-name sort is the default UTF-16 order (== code-point order for the
 *     BMP; field names are normally ASCII).
 */

import { getFingerprintWasmBackend } from "./fingerprintWasmBackend.js";

const US = 0x1f; // unit separator: between a field name and its value
const RS = 0x1e; // record separator: end of one field
const _enc = new TextEncoder();

/**
 * Whether a record can be fingerprinted via the shared wasm kernel (which takes
 * a JSON object string) with a result BYTE-IDENTICAL to the pure-TS path. JSON
 * can't carry `bigint` / `Uint8Array`, and a handful of value/name shapes hash
 * differently through a JSON round-trip than through `_valueBytes`, so those
 * stay on the pure-TS path (which is the reference the wasm kernel matches for
 * everything else). Only HASHED (non-`__`) fields are inspected — `__`-prefixed
 * keys are dropped by both surfaces.
 *
 * Ineligible (→ pure-TS):
 *   - `bigint` / `Uint8Array` — not JSON-representable.
 *   - `undefined` — `JSON.stringify` drops the key; pure-TS hashes it as null.
 *   - `-0` or a non-safe integer — JSON emits `"0"` / loses precision, so the
 *     int/float tag or digits would differ.
 *   - a non-finite number, or a nested array/object — both surfaces throw; we
 *     keep pure-TS's exact error.
 *   - a non-ASCII field name — pure-TS sorts by UTF-16, the kernel by UTF-8
 *     bytes; identical for ASCII, so restrict to ASCII to preserve the id.
 */
function _isWasmEligible(record: Record<string, unknown>): boolean {
  for (const name of Object.keys(record)) {
    if (name.startsWith("__")) continue; // dropped by both surfaces
    // eslint-disable-next-line no-control-regex
    if (!/^[\x00-\x7f]*$/.test(name)) return false; // ASCII names only
    const v = record[name];
    if (v === null) continue;
    const t = typeof v;
    if (t === "boolean" || t === "string") continue;
    if (t === "number") {
      const n = v as number;
      if (!Number.isFinite(n)) return false;
      if (Number.isInteger(n) && (Object.is(n, -0) || !Number.isSafeInteger(n)))
        return false;
      continue;
    }
    return false; // bigint, Uint8Array, undefined, object, symbol, function
  }
  return true;
}

function _hex(bytes: Uint8Array): string {
  let out = "";
  for (let i = 0; i < bytes.length; i++) out += bytes[i]!.toString(16).padStart(2, "0");
  return out;
}

function _valueBytes(name: string, v: unknown): number[] {
  if (v === null || v === undefined) return [0x6e]; // 'n'
  if (typeof v === "boolean") return [0x62, v ? 0x31 : 0x30]; // 'b' '1'/'0'
  if (typeof v === "bigint") return [0x69, ..._enc.encode(v.toString())]; // 'i'
  if (typeof v === "number") {
    if (Number.isInteger(v) && !Object.is(v, -0)) {
      return [0x69, ..._enc.encode(String(v))]; // 'i' + decimal
    }
    if (!Number.isFinite(v)) {
      throw new RangeError(`field ${JSON.stringify(name)}: non-finite float is not canonicalizable`);
    }
    const norm = v === 0 ? 0 : v; // collapse -0 -> 0 (matches Python -0.0 -> 0.0)
    const buf = new ArrayBuffer(8);
    new DataView(buf).setFloat64(0, norm, false); // big-endian IEEE-754
    return [0x66, ..._enc.encode(_hex(new Uint8Array(buf)))]; // 'f' + 16 hex
  }
  if (typeof v === "string") return [0x73, ..._enc.encode(v)]; // 's'
  if (v instanceof Uint8Array) return [0x79, ...v]; // 'y'
  throw new TypeError(
    `field ${JSON.stringify(name)}: unsupported value type ${typeof v} ` +
      "(v1 record fingerprint is primitive-only: null/boolean/number/bigint/string/Uint8Array)",
  );
}

/**
 * Canonical SHA-256 fingerprint (64 lowercase hex) of a record's content
 * fields. `__`-prefixed keys are dropped. Returns the same value the Python,
 * native C ABI, and SQL surfaces produce for the same record.
 */
export async function recordFingerprint(record: Record<string, unknown>): Promise<string> {
  // When the opt-in wasm backend is enabled, run the SHARED fingerprint-core
  // kernel for JSON-primitive-safe records — one canonicalizer across every
  // surface. bigint / Uint8Array / edge-case records stay on the pure-TS path
  // below (the reference the kernel matches). A JSON.stringify throw (e.g. a
  // bigint hiding in a `__`-prefixed value) also falls through to pure-TS.
  const backend = getFingerprintWasmBackend();
  if (backend !== null && _isWasmEligible(record)) {
    try {
      return backend.fingerprintJson(JSON.stringify(record));
    } catch {
      // fall through to the pure-TS canonicalizer
    }
  }
  const names = Object.keys(record)
    .filter((k) => !k.startsWith("__"))
    .sort();
  const bytes: number[] = [];
  for (const name of names) {
    for (const b of _enc.encode(name)) bytes.push(b);
    bytes.push(US);
    for (const b of _valueBytes(name, record[name])) bytes.push(b);
    bytes.push(RS);
  }
  const digest = await crypto.subtle.digest("SHA-256", new Uint8Array(bytes));
  return _hex(new Uint8Array(digest));
}
