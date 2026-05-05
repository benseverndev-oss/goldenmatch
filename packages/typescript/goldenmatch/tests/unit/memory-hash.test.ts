import { describe, it, expect } from "vitest";
import {
  sha256_16,
  computeFieldHash,
  computeRecordHash,
  computeRecordHashes,
} from "../../src/core/memory/hash.js";

// Pinned SHA-256 hex[:16] values (locked cross-language contract).
// These match Python's `hashlib.sha256(s.encode()).hexdigest()[:16]` semantics.
// Computed via Node's crypto.createHash("sha256").update(s, "utf8").digest("hex").slice(0,16),
// which is bit-for-bit identical to Python's hashlib for the same UTF-8 bytes.
//   "hello"      -> 2cf24dba5fb0a30e
//   "café"       -> 850f7dc43910ff89
//   "a|1|b|2"    -> 872047a43837e344
//   "Acme|10001" -> 154932f8516111dc

const HASH_HELLO = "2cf24dba5fb0a30e";
const HASH_CAFE = "850f7dc43910ff89";
const HASH_A1B2 = "872047a43837e344";
const HASH_ACME = "154932f8516111dc";

describe("sha256_16", () => {
  it("hashes 'hello' to the pinned value", async () => {
    expect(await sha256_16("hello")).toBe(HASH_HELLO);
  });

  it("hashes UTF-8 multi-byte 'café' to the pinned value", async () => {
    expect(await sha256_16("café")).toBe(HASH_CAFE);
  });

  it("returns a 16-character hex string", async () => {
    const h = await sha256_16("anything");
    expect(h).toMatch(/^[0-9a-f]{16}$/);
  });
});

describe("computeFieldHash", () => {
  it("joins values with '|' and matches pinned 'a|1|b|2' hash", async () => {
    expect(await computeFieldHash(["a", "1"], ["b", "2"])).toBe(HASH_A1B2);
  });

  it("coerces non-string values via String()", async () => {
    // Python uses str(v) which renders ints as "1","2" — same as JS String().
    expect(await computeFieldHash(["a", 1], ["b", 2])).toBe(HASH_A1B2);
  });
});

describe("computeRecordHash", () => {
  it("excludes __row_id__, sorts columns, joins values with '|' (Acme/10001 pinned)", async () => {
    // sorted content cols: ["name","zip"] -> values "Acme","10001" -> "Acme|10001"
    const row = { name: "Acme", zip: "10001", __row_id__: 42 };
    expect(await computeRecordHash(row, ["name", "zip", "__row_id__"])).toBe(
      HASH_ACME,
    );
  });

  it("produces same hash for same content with different __row_id__", async () => {
    const a = { name: "Acme", zip: "10001", __row_id__: 1 };
    const b = { name: "Acme", zip: "10001", __row_id__: 999 };
    expect(await computeRecordHash(a, ["name", "zip", "__row_id__"])).toBe(
      await computeRecordHash(b, ["name", "zip", "__row_id__"]),
    );
  });

  it("ignores input column order (sorts internally)", async () => {
    const row = { name: "Acme", zip: "10001", __row_id__: 42 };
    expect(await computeRecordHash(row, ["zip", "__row_id__", "name"])).toBe(
      HASH_ACME,
    );
  });
});

describe("computeRecordHashes", () => {
  it("returns Map<rowId, hash> matching single-row computeRecordHash", async () => {
    const rows = [
      { name: "Acme", zip: "10001", __row_id__: 1 },
      { name: "Beta", zip: "20002", __row_id__: 2 },
      { name: "Acme", zip: "10001", __row_id__: 3 }, // dup content, diff rid
    ];
    const cols = ["name", "zip", "__row_id__"];
    const map = await computeRecordHashes(rows, cols);

    expect(map.size).toBe(3);
    expect(map.get(1)).toBe(await computeRecordHash(rows[0]!, cols));
    expect(map.get(2)).toBe(await computeRecordHash(rows[1]!, cols));
    expect(map.get(3)).toBe(await computeRecordHash(rows[2]!, cols));
    // Same content => same hash
    expect(map.get(1)).toBe(map.get(3));
    // The Acme row matches the pinned value
    expect(map.get(1)).toBe(HASH_ACME);
  });
});
