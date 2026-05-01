// One-shot regenerator for tests/fixtures/_goldens/*.json from the TS side.
// Use when the canonical Python generator (scripts/gen_parity_goldens.py)
// can't run for some reason. Run from packages/infermap-js after `npm run build`.
//
//   node scripts/regen-parity-goldens.mjs
//
// Goldens written by this script must be re-verified against Python before
// merging — they're a stopgap, not the source of truth.

import { readFileSync, writeFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "../../..");
const MANIFEST_PATH = resolve(REPO_ROOT, "tests/fixtures/parity_cases.json");
const GOLDENS_DIR = resolve(REPO_ROOT, "tests/fixtures/_goldens");

const { MapEngine } = await import("../dist/core/index.js");
const { inferSchemaFromRecords } = await import("../dist/core/index.js");

const PRECISION = 4;
function round(n) {
  return Math.round(n * 10 ** PRECISION) / 10 ** PRECISION;
}

const manifest = JSON.parse(readFileSync(MANIFEST_PATH, "utf8"));

for (const kase of manifest.cases) {
  if (kase.source.kind !== "records" || kase.target.kind !== "records") {
    console.log(`skip ${kase.name}: non-records input`);
    continue;
  }
  const src = inferSchemaFromRecords(kase.source.records);
  const tgt = inferSchemaFromRecords(kase.target.records);
  const engine = new MapEngine({ minConfidence: kase.min_confidence });
  const result = engine.mapSchemas(src, tgt);

  const mappings = result.mappings
    .map((m) => ({
      source: m.source,
      target: m.target,
      confidence: round(m.confidence),
    }))
    .sort((a, b) =>
      a.source === b.source
        ? a.target.localeCompare(b.target)
        : a.source.localeCompare(b.source)
    );

  const golden = {
    min_confidence: kase.min_confidence,
    mappings,
    unmapped_source: [...result.unmappedSource].sort(),
    unmapped_target: [...result.unmappedTarget].sort(),
  };

  const out = resolve(GOLDENS_DIR, `${kase.name}.json`);
  writeFileSync(out, JSON.stringify(golden, null, 2) + "\n", "utf8");
  console.log(`wrote ${kase.name}.json (${mappings.length} mappings)`);
}
