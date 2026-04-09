// Example 8 — Score-matrix introspection (new in v0.2)
//
// Demonstrates the `returnScoreMatrix: true` engine flag and the
// `mapSchemas()` API. Useful when you want to:
//
// - show users the runners-up for low-confidence picks,
// - build a UI for manual override,
// - compute your own ranking metrics on top of the engine output.
//
// Run:
//   npx tsx examples/typescript/08_score_matrix_introspection.ts

import { MapEngine, makeFieldInfo, makeSchemaInfo } from "infermap";

function fields(spec: ReadonlyArray<[string, string, string[]]>) {
  return spec.map(([name, dtype, samples]) =>
    makeFieldInfo({ name, dtype, sampleValues: samples, valueCount: samples.length }),
  );
}

const source = makeSchemaInfo({
  sourceName: "messy_export",
  fields: fields([
    ["cust_id", "int64", ["1", "2", "3"]],
    ["e_mail", "string", ["a@x.io", "b@y.io", "c@z.io"]],
    ["amt", "float64", ["19.99", "42.00", "7.50"]],
    ["dt", "string", ["2026-01-01", "2026-01-02", "2026-01-03"]],
  ]),
});

const target = makeSchemaInfo({
  sourceName: "warehouse_orders",
  fields: fields([
    ["customer_id", "int64", ["100", "200", "300"]],
    ["email", "string", ["x@a.io", "y@b.io", "z@c.io"]],
    ["amount_usd", "float64", ["12.34", "56.78", "9.10"]],
    ["order_date", "string", ["2026-02-01", "2026-02-02", "2026-02-03"]],
    ["notes", "string", ["", "", ""]], // distractor
  ]),
});

// Opt into the score matrix so we can inspect runners-up.
const engine = new MapEngine({ returnScoreMatrix: true });

// mapSchemas() bypasses schema extraction — use it when you already
// have SchemaInfo in hand. Mirrors the Python `map_schemas()` API.
const result = engine.mapSchemas(source, target);

console.log("=== Top picks (above min confidence) ===");
for (const m of result.mappings) {
  console.log(`  ${m.source.padStart(10)} -> ${m.target.padEnd(14)} (conf=${m.confidence.toFixed(3)})`);
}

console.log("\n=== Top-3 candidates per source field ===");
const matrix = result.scoreMatrix ?? {};
for (const [src, candidates] of Object.entries(matrix)) {
  const ranked = Object.entries(candidates)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 3)
    .map(([tgt, s]) => `${tgt}=${s.toFixed(3)}`)
    .join(", ");
  console.log(`  ${src.padStart(10)}: ${ranked}`);
}

console.log(`\nUnmapped source: ${JSON.stringify(result.unmappedSource)}`);
console.log(`Unmapped target: ${JSON.stringify(result.unmappedTarget)}`);
