/**
 * Configured transform — explicit transforms per column.
 *
 * Usage: npx tsx examples/typescript/configured-transform.ts
 */

import { TransformEngine } from "goldenflow";

const data = [
  { name: "  john smith  ", email: "JOHN@EXAMPLE.COM", phone: "(555) 123-4567", state: "Pennsylvania", price: "$1,234.56" },
  { name: "DR. JANE DOE", email: "jane@test.com", phone: "555.987.6543", state: "new york", price: "$99.99" },
  { name: "bob JOHNSON", email: "bob@test.com", phone: "5554567890", state: "CA", price: "$0.50" },
];

const engine = new TransformEngine({
  transforms: [
    { column: "name", ops: ["strip", "strip_titles", "title_case"] },
    { column: "email", ops: ["strip", "email_normalize"] },
    { column: "phone", ops: ["phone_e164"] },
    { column: "state", ops: ["state_abbreviate"] },
    { column: "price", ops: ["currency_strip"] },
  ],
  renames: {},
  drop: [],
  dedup: { columns: ["email"], keep: "first" },
});

const result = engine.transformDf(data);

console.log("=== Configured Transform ===\n");
for (const row of result.rows) {
  console.log(row);
}

console.log(`\n${result.manifest.records.length} transforms applied, ${result.rows.length} rows (after dedup)`);
