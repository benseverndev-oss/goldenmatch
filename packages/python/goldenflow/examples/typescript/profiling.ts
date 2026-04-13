/**
 * Profiling — inspect column types, nulls, and patterns.
 *
 * Usage: npx tsx examples/typescript/profiling.ts
 */

import { profileDataframe, learnConfig, printProfile } from "goldenflow";

const data = [
  { name: "John Smith", email: "john@example.com", zip: "01234", phone: "(555) 123-4567", age: 32 },
  { name: "Jane Doe", email: "jane@test.com", zip: "90210", phone: "555-987-6543", age: 28 },
  { name: null, email: "bob@company.org", zip: "12345-6789", phone: "+1 555 456 7890", age: null },
  { name: "Alice Williams", email: null, zip: "02134", phone: "5554567890", age: 45 },
];

// Profile the data
const profile = profileDataframe(data, "sample_data");

console.log("=== Data Profile ===\n");
printProfile(profile);

// Auto-generate a config from the profile
console.log("\n\n=== Suggested Config ===\n");
const config = learnConfig(data, "sample_data");
for (const spec of config.transforms) {
  console.log(`  ${spec.column}: [${spec.ops.join(", ")}]`);
}
