/**
 * SHA-256 hash module — cross-language byte parity with Python.
 *
 * Mirrors `packages/python/goldenmatch/goldenmatch/core/memory/corrections.py`:
 *   - SHA-256 of UTF-8 bytes, truncated to 16 hex chars
 *   - Values are joined with "|" only (no "<col>=<val>" formatting)
 *   - record_hash excludes "__row_id__" and sorts remaining columns alphabetically
 *
 * Edge-safe: uses the global Web Crypto API (crypto.subtle.digest), available
 * in Node 20+, browsers, and edge runtimes. MUST NOT import `node:*`.
 */

const ROW_ID_COL = "__row_id__";

function bytesToHex(buffer: ArrayBuffer): string {
  const view = new Uint8Array(buffer);
  let out = "";
  for (let i = 0; i < view.length; i++) {
    out += view[i]!.toString(16).padStart(2, "0");
  }
  return out;
}

/**
 * SHA-256 of `s` encoded as UTF-8, hex-encoded and truncated to 16 chars.
 * Equivalent to Python's `hashlib.sha256(s.encode()).hexdigest()[:16]`.
 */
export async function sha256_16(s: string): Promise<string> {
  const bytes = new TextEncoder().encode(s);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return bytesToHex(digest).slice(0, 16);
}

/**
 * Hash matched field values for staleness detection.
 *
 * Concatenates rowAVals + rowBVals (in order), coerces each to string,
 * joins with "|", and hashes. Mirrors `compute_field_hash` in Python.
 */
export async function computeFieldHash(
  rowAVals: readonly unknown[],
  rowBVals: readonly unknown[],
): Promise<string> {
  const combined = [...rowAVals, ...rowBVals].map((v) => String(v)).join("|");
  return sha256_16(combined);
}

/**
 * Hash content fields (sorted by name, __row_id__ excluded) for entity
 * identity check. Two runs over the same content produce the same hash
 * even if row order changes. Mirrors `compute_record_hash` in Python.
 */
export async function computeRecordHash(
  row: Record<string, unknown>,
  columns: readonly string[],
): Promise<string> {
  const contentCols = columns.filter((c) => c !== ROW_ID_COL).slice().sort();
  const joined = contentCols.map((c) => String(row[c])).join("|");
  return sha256_16(joined);
}

/**
 * Vectorized batch: returns Map<rowId, recordHash> for every row.
 * Mirrors `_build_hash_to_rids` in Python (but keyed by row_id, not hash).
 *
 * Each row must contain a `__row_id__` field (number).
 */
export async function computeRecordHashes(
  rows: readonly Record<string, unknown>[],
  columns: readonly string[],
): Promise<Map<number, string>> {
  const contentCols = columns.filter((c) => c !== ROW_ID_COL).slice().sort();
  const hashes = await Promise.all(
    rows.map((row) => sha256_16(contentCols.map((c) => String(row[c])).join("|"))),
  );
  const out = new Map<number, string>();
  for (let i = 0; i < rows.length; i++) {
    out.set(rows[i]![ROW_ID_COL] as number, hashes[i]!);
  }
  return out;
}
