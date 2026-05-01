/**
 * Streaming — process large datasets in chunks.
 *
 * Usage: npx tsx examples/typescript/streaming.ts
 */

import { StreamProcessor } from "goldenflow";

// Simulate a large dataset (1000 rows)
const largeDataset = Array.from({ length: 1000 }, (_, i) => ({
  id: i + 1,
  name: `  Person ${i + 1}  `,
  email: `USER${i + 1}@EXAMPLE.COM`,
  status: i % 7 === 0 ? "actve" : i % 3 === 0 ? "ACTIVE" : "active",
}));

const processor = new StreamProcessor({
  transforms: [
    { column: "name", ops: ["strip", "title_case"] },
    { column: "email", ops: ["strip", "lowercase"] },
  ],
});

console.log("=== Streaming Transform ===\n");

let totalRows = 0;
const chunkSize = 250;

for (const result of processor.streamRows(largeDataset, chunkSize)) {
  totalRows += result.rows.length;
  console.log(
    `Batch ${processor.batchesProcessed}: ${result.rows.length} rows, ` +
    `${result.manifest.records.length} transforms applied`,
  );
}

console.log(`\nTotal: ${totalRows} rows processed in ${processor.batchesProcessed} batches`);
