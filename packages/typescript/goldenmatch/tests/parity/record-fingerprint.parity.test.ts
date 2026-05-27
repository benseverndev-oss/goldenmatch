/**
 * record-fingerprint.parity.test.ts — TS recordFingerprint vs the canonical spec.
 *
 * The pinned vectors are computed from the canonical bytes (independent of any
 * implementation) and are identical to the Python
 * tests/test_record_fingerprint.py::_PINNED + the fingerprint-core Rust unit
 * tests + the pgrx pg_test. If these hold, the TS surface mints the same
 * cross-surface stable record id as Python / native C ABI / DuckDB / Postgres.
 */
import { describe, it, expect } from "vitest";
import { recordFingerprint } from "../../src/core/record-fingerprint.js";

// {}        -> sha256("")
// {"a":"x"} -> sha256("a" 1f "s" "x" 1e)
// {"a":1}   -> sha256("a" 1f "i" "1" 1e)
// {"n":1.5} -> sha256("n" 1f "f" "3ff8000000000000" 1e)
const PINNED: ReadonlyArray<[Record<string, unknown>, string]> = [
  [{}, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"],
  [{ a: "x" }, "7381d5ba2dac5be0af49232a3209ab8d0dc2e4ed804a60ce533fdfe5254307e3"],
  [{ a: 1 }, "b42e38730ddd9a099426dffa93926c03258ee2cd93f75204daa6f989af628206"],
  [{ n: 1.5 }, "241b8cd11b575fd2b21e90b490f57fac54930f9a12124f23e284caa200c403a9"],
];

describe("recordFingerprint cross-surface parity", () => {
  it.each(PINNED)("matches the pinned vector for %o", async (record, expected) => {
    expect(await recordFingerprint(record)).toBe(expected);
  });

  it("is 64 lowercase hex", async () => {
    const fp = await recordFingerprint({ a: "x", b: 2 });
    expect(fp).toMatch(/^[0-9a-f]{64}$/);
  });

  it("is key-order independent", async () => {
    expect(await recordFingerprint({ a: 1, b: 2 })).toBe(
      await recordFingerprint({ b: 2, a: 1 }),
    );
  });

  it("drops __-prefixed fields", async () => {
    expect(await recordFingerprint({ a: 1, __row_id__: 9 })).toBe(
      await recordFingerprint({ a: 1 }),
    );
  });

  it("type-tags distinguish int / string / bool", async () => {
    const fps = new Set([
      await recordFingerprint({ a: 1 }),
      await recordFingerprint({ a: "1" }),
      await recordFingerprint({ a: true }),
    ]);
    expect(fps.size).toBe(3);
  });

  it("rejects non-finite floats", async () => {
    await expect(recordFingerprint({ a: NaN })).rejects.toThrow();
    await expect(recordFingerprint({ a: Infinity })).rejects.toThrow();
  });

  it("rejects unsupported value types", async () => {
    await expect(recordFingerprint({ a: [1, 2, 3] })).rejects.toThrow();
  });
});
