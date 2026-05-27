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

const US = 0x1f; // unit separator: between a field name and its value
const RS = 0x1e; // record separator: end of one field
const _enc = new TextEncoder();

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
