/**
 * 01 — 30-second quickstart, TypeScript edition.
 *
 * Same shape as the Python quickstart. Reads JSON rows, deduplicates, prints.
 *
 * Run:
 *     npm install goldenmatch
 *     npx tsx 01-quickstart.ts
 */
import { dedupe } from "goldenmatch";

const rows = [
  { id: 1, name: "Jane Smith",   email: "jane@example.com", zip: "10001" },
  { id: 2, name: "Jane Smyth",   email: "jane@example.com", zip: "10001" },
  { id: 3, name: "Robert Jones", email: "bob@example.com",  zip: "94110" },
  { id: 4, name: "Bob Jones",    email: "robert.j@example.com", zip: "94110" },
  { id: 5, name: "Alice Lee",    email: "alice@example.com", zip: "60601" },
];

const result = dedupe(rows, {
  exact: ["email"],
  fuzzy: { name: 0.85 },
  blocking: ["zip"],
  threshold: 0.85,
});

console.log(result.stats);
// { totalRecords: 5, totalClusters: 4, matchRate: 0.2, ... }

console.log(`\nclusters (${result.clusters.size}):`);
for (const [id, cluster] of result.clusters) {
  if (cluster.members.length > 1) {
    console.log(`  cluster ${id}: rows ${cluster.members.join(", ")}`);
  }
}
