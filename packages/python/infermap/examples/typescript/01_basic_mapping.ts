// 01 — Basic mapping: plain records → plain records.
//
// Run: npx tsx examples/typescript/01_basic_mapping.ts
//
// Shows the simplest path — pass two arrays of record objects to map()
// and let infermap infer columns, dtypes, and the optimal field pairing.

import { map, mapResultToJson } from "infermap";

const crmRecords = [
  { cust_id: "C001", fname: "John", lname: "Doe", email_addr: "john@acme.com", tel: "555-0100" },
  { cust_id: "C002", fname: "Jane", lname: "Smith", email_addr: "jane@globex.com", tel: "555-0200" },
  { cust_id: "C003", fname: "Bob", lname: "Johnson", email_addr: "bob@initech.com", tel: "555-0300" },
];

const erpRecords = [
  { customer_id: "", first_name: "", last_name: "", email: "", phone: "" },
];

const result = map(
  { records: crmRecords, sourceName: "crm" },
  { records: erpRecords, sourceName: "erp" }
);

console.log("== Mappings ==");
for (const m of result.mappings) {
  console.log(`  ${m.source.padEnd(12)} → ${m.target.padEnd(14)} (conf=${m.confidence.toFixed(3)})`);
}

if (result.unmappedSource.length > 0) {
  console.log(`Unmapped source: ${result.unmappedSource.join(", ")}`);
}
if (result.unmappedTarget.length > 0) {
  console.log(`Unmapped target: ${result.unmappedTarget.join(", ")}`);
}

// Full JSON output (report shape, rounded confidences):
// console.log(mapResultToJson(result));
void mapResultToJson;
