// 02 — Read CSV files from disk and map them.
//
// Run from repo root: npx tsx examples/typescript/02_csv_file_mapping.ts
//
// Uses the Node-only file reader (`infermap/node`) to read CSVs from disk,
// then runs the engine directly on the extracted schemas.

import { MapEngine } from "infermap";
import { extractSchemaFromFile } from "infermap/node";
import { resolve } from "node:path";

const HERE = new URL(".", import.meta.url).pathname;
const DATA = resolve(HERE, "..", "data");

async function main() {
  const source = await extractSchemaFromFile(resolve(DATA, "crm_export.csv"));
  const target = await extractSchemaFromFile(resolve(DATA, "erp_customers.csv"));

  console.log(`Source: ${source.sourceName} — ${source.fields.length} fields`);
  console.log(`Target: ${target.sourceName} — ${target.fields.length} fields\n`);

  const engine = new MapEngine({ minConfidence: 0.3 });
  const result = engine.mapSchemas(source, target);

  console.log("SOURCE            TARGET            CONF   REASONING");
  console.log("-".repeat(80));
  for (const m of result.mappings) {
    const short =
      m.reasoning.length > 40 ? m.reasoning.slice(0, 40) + "..." : m.reasoning;
    console.log(
      `${m.source.padEnd(17)} ${m.target.padEnd(17)} ${m.confidence.toFixed(3).padStart(5)}  ${short}`
    );
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
