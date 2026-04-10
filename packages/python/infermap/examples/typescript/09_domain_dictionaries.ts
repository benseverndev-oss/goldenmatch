// Example 9 — Domain dictionaries (new in v0.3)
//
// Shows how to boost mapping accuracy on domain-specific schemas.
// Without the dictionary, `mrn` and `patient_id` rely on fuzzy similarity.
// With `domains: ["healthcare"]`, the AliasScorer fires at 0.95.
//
// Run:
//   npx tsx examples/typescript/09_domain_dictionaries.ts

import {
  MapEngine,
  makeFieldInfo,
  makeSchemaInfo,
  availableDomains,
} from "infermap";

const f = (name: string, dtype: string, samples: string[]) =>
  makeFieldInfo({ name, dtype, sampleValues: samples, valueCount: samples.length });

console.log("Available domains:", availableDomains());

// --- Healthcare ---
const ehrSource = makeSchemaInfo({
  sourceName: "epic_extract",
  fields: [
    f("MRN", "string", ["MRN001", "MRN002"]),
    f("DOB", "string", ["1980-01-15", "1992-06-30"]),
    f("admit_dt", "string", ["2026-01-01", "2026-01-02"]),
    f("dx_code", "string", ["E11.9", "I10"]),
  ],
});

const researchTarget = makeSchemaInfo({
  sourceName: "research_datamart",
  fields: [
    f("patient_id", "string", ["P100", "P200"]),
    f("date_of_birth", "string", ["1990-01-01", "1988-04-12"]),
    f("admission_date", "string", ["2026-02-01", "2026-02-02"]),
    f("diagnosis_code", "string", ["G40.901", "N18.6"]),
  ],
});

console.log("\n=== Without domain dictionary ===");
const base = new MapEngine();
for (const m of base.mapSchemas(ehrSource, researchTarget).mappings) {
  console.log(`  ${m.source.padStart(10)} -> ${m.target.padEnd(16)} conf=${m.confidence.toFixed(3)}`);
}

console.log("\n=== With domains: ['healthcare'] ===");
const hc = new MapEngine({ domains: ["healthcare"] });
for (const m of hc.mapSchemas(ehrSource, researchTarget).mappings) {
  console.log(`  ${m.source.padStart(10)} -> ${m.target.padEnd(16)} conf=${m.confidence.toFixed(3)}`);
}

// --- Finance ---
const ledger = makeSchemaInfo({
  sourceName: "ledger",
  fields: [
    f("txn_id", "string", ["T1", "T2"]),
    f("amt", "float", ["19.99", "42.00"]),
    f("ccy", "string", ["USD", "EUR"]),
  ],
});

const warehouse = makeSchemaInfo({
  sourceName: "warehouse",
  fields: [
    f("transaction_id", "string", ["X1", "X2"]),
    f("amount", "float", ["100.0", "200.0"]),
    f("currency", "string", ["GBP", "JPY"]),
  ],
});

console.log("\n=== Finance domain ===");
const fin = new MapEngine({ domains: ["finance"] });
for (const m of fin.mapSchemas(ledger, warehouse).mappings) {
  console.log(`  ${m.source.padStart(10)} -> ${m.target.padEnd(16)} conf=${m.confidence.toFixed(3)}`);
}
