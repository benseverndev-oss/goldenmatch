/**
 * Generate sample data with realistic quality issues for the `demo` command.
 * Port of goldencheck/cli/demo_data.py.
 * Edge-safe: returns plain row records (no file I/O).
 *
 * NOTE: Python seeds the stdlib `random` module; goldencheck-js uses a
 * Mulberry32 PRNG (see packages/python/goldencheck/CLAUDE.md "TypeScript Port"),
 * so the random column *values* differ from the Python output. The dataset
 * SHAPE and the deterministic, index-based quality-issue injections match
 * the Python sibling exactly.
 */

import type { Row } from "../data.js";
import { createRng } from "../stats.js";

export interface DemoOptions {
  /** Number of rows to generate. Default 200 (matches Python). */
  rows?: number;
  /** PRNG seed. Default 42 (matches Python's random.seed(42)). */
  seed?: number;
}

const STATUSES = ["active", "inactive", "pending"];

/**
 * Build the demo dataset as an array of row records.
 *
 * Columns: customer_id, name, email, age, phone, status, purchase_amount.
 * Injects the same set of issues at the same indices as the Python version:
 * bad emails (3, 17, 42), out-of-range ages (5, 88), null age (120),
 * malformed phones (10, 30), case-inconsistent status (50, 51), an outlier
 * purchase_amount (0), and null/empty names (15, 16, 99).
 */
export function generateDemoRecords(options?: DemoOptions): Row[] {
  const n = options?.rows ?? 200;
  const rng = createRng(options?.seed ?? 42);

  const randInt = (lo: number, hi: number): number => lo + Math.floor(rng() * (hi - lo + 1));
  const randFloat = (lo: number, hi: number): number => lo + rng() * (hi - lo);
  const choice = <T>(arr: readonly T[]): T => arr[Math.floor(rng() * arr.length)]!;

  const names: (string | null)[] = Array.from({ length: n }, (_, i) => `Customer ${i}`);
  const emails: string[] = Array.from({ length: n }, (_, i) => `user${i}@example.com`);
  const ages: (number | null)[] = Array.from({ length: n }, () => randInt(18, 85));
  const phones: string[] = Array.from(
    { length: n },
    () => `555-${randInt(100, 999)}-${randInt(1000, 9999)}`,
  );
  const status: string[] = Array.from({ length: n }, () => choice(STATUSES));
  const amounts: number[] = Array.from(
    { length: n },
    () => Math.round(randFloat(10.0, 5000.0) * 100) / 100,
  );

  // Inject quality issues (deterministic, by index — matches Python exactly).
  if (n > 42) {
    emails[3] = "not-an-email";
    emails[17] = "also bad";
    emails[42] = "";
  }
  if (n > 120) {
    ages[5] = -3;
    ages[88] = 200;
    ages[120] = null;
  }
  if (n > 30) {
    phones[10] = "12345";
    phones[30] = "abc-def-ghij";
  }
  if (n > 51) {
    status[50] = "Active"; // case inconsistency
    status[51] = "ACTIVE";
  }
  amounts[0] = 999999.99; // outlier
  if (n > 99) {
    names[15] = null;
    names[16] = null;
    names[99] = "";
  }

  const records: Row[] = [];
  for (let i = 0; i < n; i++) {
    records.push({
      customer_id: i + 1,
      name: names[i] ?? null,
      email: emails[i],
      age: ages[i] ?? null,
      phone: phones[i],
      status: status[i],
      purchase_amount: amounts[i],
    });
  }
  return records;
}
