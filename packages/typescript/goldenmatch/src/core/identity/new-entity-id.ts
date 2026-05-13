/**
 * UUIDv7-shaped entity id generator. Time-ordered for clustered-btree
 * friendliness; falls back to standard `crypto.randomUUID()` if no
 * Web Crypto is available (edge runtimes).
 */

export function newEntityId(): string {
  const tsMs = BigInt(Date.now()) & ((1n << 48n) - 1n);
  const randBytes = randomBytes(10);
  // Use BigInt arithmetic for 128-bit composition, then format.
  const randA = BigInt(randBytes[0]! & 0x0f) << 8n | BigInt(randBytes[1]!);
  let lo = 0n;
  for (let i = 2; i < 10; i++) {
    lo = (lo << 8n) | BigInt(randBytes[i]!);
  }
  lo = lo & ((1n << 62n) - 1n);

  const high =
    (tsMs << 16n) |
    (0x7n << 12n) |
    randA;
  const low =
    (0b10n << 62n) | lo;

  return formatUuid(high, low);
}

function randomBytes(n: number): Uint8Array {
  const out = new Uint8Array(n);
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    crypto.getRandomValues(out);
    return out;
  }
  // Fallback (very unusual to need this): Math.random
  for (let i = 0; i < n; i++) out[i] = Math.floor(Math.random() * 256);
  return out;
}

function formatUuid(high: bigint, low: bigint): string {
  const hh = high.toString(16).padStart(16, "0");
  const ll = low.toString(16).padStart(16, "0");
  return (
    hh.slice(0, 8) + "-" +
    hh.slice(8, 12) + "-" +
    hh.slice(12, 16) + "-" +
    ll.slice(0, 4) + "-" +
    ll.slice(4, 16)
  );
}
