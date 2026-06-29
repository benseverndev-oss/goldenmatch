#!/usr/bin/env node
/**
 * bench_goldenprofile_wasm.mjs — scale / dist-validation bench for the
 * GoldenProfile wasm surface.
 *
 * This is an ENABLEMENT bench (the healer track), NOT a speedup-vs-pure-TS gate:
 * there is no pure-TS resolver to beat. Its job is to prove
 *   1. the JS<->WASM boundary is crossed ONCE per resolve (batch, not per-pair),
 *   2. the artifact survives a large profile set without the large-array
 *      footgun (ADR 0014's `Math.min(...vals)` stack-overflow class of bug),
 *   3. the wall is sane at scale.
 *
 * Default N = 10000 profiles (override: `node scripts/bench_goldenprofile_wasm.mjs 50000`).
 * Loads the committed wasm artifact directly (no package build needed).
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import {
  initSync,
  resolve_json,
} from "../src/core/_wasm/goldenprofileWasmBindings.js";

const here = dirname(fileURLToPath(import.meta.url));
const N = Number(process.argv[2] ?? 10000);

initSync({
  module: readFileSync(
    resolve(
      here,
      "../../../rust/extensions/goldenprofile-wasm/pkg/goldenprofile_wasm_bg.wasm",
    ),
  ),
});

// Build N profiles: `entities` distinct companies, each with a few mention
// variants (so there's real merging work + a known-ish cluster floor).
const VARIANTS = 4;
const entities = Math.ceil(N / VARIANTS);
const suffixes = ["", " Inc", " LLC", " Corp", " Co"];
const profiles = [];
for (let e = 0; e < entities && profiles.length < N; e++) {
  for (let v = 0; v < VARIANTS && profiles.length < N; v++) {
    profiles.push({
      kind: "node",
      name: `Company ${e}${suffixes[v % suffixes.length]}`,
      category: "Company",
      anchor: "UNKNOWN",
      attribute: `attr ${e}`,
    });
  }
}

const request = JSON.stringify({ profiles });
console.log(`profiles: ${profiles.length}  (~${entities} entities x ${VARIANTS} variants)`);

// Warm + timed runs; report median wall.
const runs = [];
let lastClusters = 0;
let lastEdges = 0;
for (let i = 0; i < 5; i++) {
  const t0 = performance.now();
  const out = JSON.parse(resolve_json(request));
  const dt = performance.now() - t0;
  runs.push(dt);
  lastClusters = out.clusters.length;
  lastEdges = out.edges.length;
}
runs.sort((a, b) => a - b);
const median = runs[Math.floor(runs.length / 2)];

console.log(`clusters: ${lastClusters}  edges: ${lastEdges}`);
console.log(`wall (median of ${runs.length}): ${median.toFixed(1)} ms`);
console.log(`runs ms: ${runs.map((r) => r.toFixed(0)).join(", ")}`);

// Guards: it ran (no crash) and produced a sane partition.
if (lastClusters < 1 || lastClusters > profiles.length) {
  console.error(`FAIL: implausible cluster count ${lastClusters}`);
  process.exit(1);
}
console.log(`\n>>> PASS: resolved ${profiles.length} profiles -> ${lastClusters} clusters, no large-array crash`);
