/**
 * Zero-config transform — auto-detect and fix messy data.
 *
 * Usage: npx tsx examples/typescript/zero-config.ts
 */

import { TransformEngine } from "goldenflow";

const messyData = [
  {
    name: "  John Smith  ",
    email: "JOHN@EXAMPLE.COM",
    phone: "(555) 123-4567",
    status: "active",
    signup_date: "03/15/2024",
  },
  {
    name: "DR. JANE DOE",
    email: "  jane+work@gmail.com  ",
    phone: "555.987.6543",
    status: "ACTIVE",
    signup_date: "2024-01-20",
  },
  {
    name: "  Bob Johnson  ",
    email: "bob@test.com",
    phone: "+1-555-456-7890",
    status: "actve",
    signup_date: "Jan 5, 2023",
  },
];

const engine = new TransformEngine();
const result = engine.transformDf(messyData);

console.log("=== Zero-Config Transform ===\n");
console.log(`Rows: ${result.rows.length}`);
console.log(`Transforms applied: ${result.manifest.records.length}`);
console.log(`Errors: ${result.manifest.errors.length}\n`);

console.log("--- Transform Audit Trail ---");
for (const record of result.manifest.records) {
  console.log(`  ${record.column}/${record.transform}: ${record.affectedRows}/${record.totalRows} rows`);
}

console.log("\n--- Cleaned Data ---");
for (const row of result.rows) {
  console.log(row);
}
