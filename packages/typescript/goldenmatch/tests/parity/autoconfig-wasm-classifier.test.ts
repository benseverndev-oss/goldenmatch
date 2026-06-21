/**
 * autoconfig-wasm-classifier.test.ts
 *
 * The column classifier in `profiler.ts` routes through the shared wasm core
 * when the opt-in backend is enabled. Unlike the planner, the wasm and pure-TS
 * classifiers are DIFFERENT implementations, so there's no byte-equivalence to
 * assert — the core's correctness is covered by the golden vectors
 * (`autoconfig-core.parity.test.ts`). This test guards the WIRING + the
 * core-13 → TS-11 `ColumnType` remap:
 *   - a shared type (email) survives 1:1,
 *   - `identifier` is remapped to `id`,
 *   - the profiler never leaks a core-only label (`identifier`/`string`/
 *     `address`/`description`) into its 11-value vocabulary,
 *   - disabling restores the pure-TS path.
 */
import { describe, it, expect, afterEach } from "vitest";
import { profileRows } from "../../src/core/profiler.js";
import type { ColumnType } from "../../src/core/profiler.js";
import {
  enableAutoconfigWasm,
  disableAutoconfigWasm,
} from "../../src/core/autoconfigWasm.js";
import { isAutoconfigWasmEnabled } from "../../src/core/autoconfigWasmBackend.js";

const TS_COLUMN_TYPES: ReadonlySet<ColumnType> = new Set<ColumnType>([
  "email",
  "phone",
  "zip",
  "date",
  "year",
  "name",
  "multi_name",
  "geo",
  "id",
  "numeric",
  "text",
]);

function rows(n: number): Record<string, string>[] {
  return Array.from({ length: n }, (_, i) => ({
    email: `user${i}@example.com`,
    customer_id: `ID${String(i).padStart(4, "0")}`,
    first_name: ["Alice", "Bob", "Carol", "Dave", "Eve"][i % 5]!,
    amount: String(100 + i),
  }));
}

afterEach(() => {
  disableAutoconfigWasm();
});

describe("autoconfig classifier: wasm path", () => {
  it("defaults to pure-TS (wasm not enabled)", () => {
    expect(isAutoconfigWasmEnabled()).toBe(false);
  });

  it("routes through the wasm core and remaps the vocabulary", () => {
    enableAutoconfigWasm();
    expect(isAutoconfigWasmEnabled()).toBe(true);

    const profile = profileRows(rows(20));
    const byName = profile.byName;

    // Shared type survives 1:1.
    expect(byName.email!.inferredType).toBe("email");
    // core `identifier` (from the `_id` name pattern) → TS `id`.
    expect(byName.customer_id!.inferredType).toBe("id");

    // No core-only label ever leaks into the TS vocabulary.
    for (const col of profile.columns) {
      expect(TS_COLUMN_TYPES.has(col.inferredType)).toBe(true);
      expect(col.confidence).toBeGreaterThanOrEqual(0);
      expect(col.confidence).toBeLessThanOrEqual(1);
    }
  });

  it("reverts to the pure-TS classifier when disabled", () => {
    enableAutoconfigWasm();
    disableAutoconfigWasm();
    expect(isAutoconfigWasmEnabled()).toBe(false);
    // Still classifies (pure-TS path) without throwing.
    const profile = profileRows(rows(10));
    expect(TS_COLUMN_TYPES.has(profile.byName.email!.inferredType)).toBe(true);
  });
});
