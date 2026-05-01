/**
 * Edge-safe usage — the core module runs in browsers, Workers, and Edge Runtime.
 * No Node.js APIs required.
 *
 * This example demonstrates using GoldenFlow in an edge/browser context.
 */

// Import only the edge-safe core (no file I/O, no Node APIs)
import {
  TransformEngine,
  makeConfig,
  profileDataframe,
  SchemaMapper,
  diffDataframes,
  listTransforms,
  registry,
  TabularData,
} from "goldenflow/core";

// All transforms are registered and available
console.log(`Edge-safe core loaded: ${registry().size} transforms available\n`);

// Transform data (works in any JS runtime)
const engine = new TransformEngine({
  transforms: [
    { column: "name", ops: ["strip", "title_case"] },
    { column: "amount", ops: ["currency_strip"] },
  ],
});

const result = engine.transformDf([
  { name: "  john doe  ", amount: "$1,234.56" },
  { name: "  JANE SMITH  ", amount: "$99.99" },
]);

console.log("Transformed:");
for (const row of result.rows) {
  console.log(`  ${row["name"]} — ${row["amount"]}`);
}

// TabularData for stats (also edge-safe)
const data = new TabularData([
  { score: 85, grade: "B" },
  { score: 92, grade: "A" },
  { score: 78, grade: "C" },
  { score: 95, grade: "A" },
]);

console.log(`\nTabularData: ${data.rowCount} rows`);
console.log(`  score: mean=${data.mean("score")}, min=${data.min("score")}, max=${data.max("score")}`);
console.log(`  grade: ${data.nUnique("grade")} unique values`);

// Diff two datasets
const before = [{ name: "John", status: "active" }];
const after = [{ name: "JOHN", status: "active" }];
const diff = diffDataframes(before, after);
console.log(`\nDiff: ${diff.totalChanges} change(s) in [${diff.changedColumns.join(", ")}]`);
