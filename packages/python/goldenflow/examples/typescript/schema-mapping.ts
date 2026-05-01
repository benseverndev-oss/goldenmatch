/**
 * Schema mapping — auto-map columns between two datasets.
 *
 * Usage: npx tsx examples/typescript/schema-mapping.ts
 */

import { SchemaMapper } from "goldenflow";

const sourceData = [
  { fname: "John", lname: "Smith", email_address: "john@example.com", phone_number: "(555) 123-4567" },
  { fname: "Jane", lname: "Doe", email_address: "jane@test.com", phone_number: "555.987.6543" },
];

const targetSchema = [
  { first_name: "", last_name: "", email: "", phone: "", city: "" },
];

const mapper = new SchemaMapper();
const mappings = mapper.map(sourceData, targetSchema);

console.log("=== Schema Mapping ===\n");
console.log("Source Column      ->  Target Column     Confidence  Tier");
console.log("─".repeat(65));
for (const m of mappings) {
  const tier = m.confidence >= 0.9 ? "auto" : m.confidence >= 0.6 ? "suggest" : "skip";
  console.log(
    `${m.source.padEnd(18)} ->  ${m.target.padEnd(18)} ${m.confidence.toFixed(3).padEnd(11)} ${tier}`,
  );
}

// Convert to config
const config = mapper.toConfig(mappings);
console.log(`\nGenerated config with ${config.mappings.length} mapping(s)`);
