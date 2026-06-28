#!/usr/bin/env node
/**
 * bench_goldengraph_wasm.mjs — scale / dist-validation bench for the GoldenGraph
 * wasm surface.
 *
 * ENABLEMENT bench (the healer track), NOT a speedup-vs-pure-TS gate — there is
 * no pure-TS engine to beat. It proves the JS<->WASM boundary is crossed once per
 * op (batch, not per-edge), the artifact survives a large graph without the
 * large-array footgun, and the wall is sane at scale.
 *
 * Default N = 10000 entities (override: `node scripts/bench_goldengraph_wasm.mjs 50000`).
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import {
  initSync,
  build_graph,
  neighborhood,
  communities,
} from "../src/core/_wasm/goldengraphWasmBindings.js";

const here = dirname(fileURLToPath(import.meta.url));
const N = Number(process.argv[2] ?? 10000);

initSync({
  module: readFileSync(
    resolve(here, "../../../rust/extensions/goldengraph-wasm/pkg/goldengraph_wasm_bg.wasm"),
  ),
});

// N entities, each as 2 mentions (so the resolver merges pairs), plus a chain of
// ceo_of-style edges so neighborhoods + communities have real work.
const mentions = [];
const edges = [];
const resolution = {};
for (let e = 0; e < N; e++) {
  const a = mentions.length;
  mentions.push({ name: `Org ${e} Inc`, typ: "Company" });
  const b = mentions.length;
  mentions.push({ name: `Org ${e}`, typ: "Company" });
  resolution[a] = e;
  resolution[b] = e;
  if (e > 0) edges.push({ subj: a, predicate: "linked_to", obj: a - 2, source_ref: `d${e}` });
}

const mJson = JSON.stringify(mentions);
const eJson = JSON.stringify(edges);
const rJson = JSON.stringify(resolution);
console.log(`mentions: ${mentions.length}  entities(target): ${N}  edges: ${edges.length}`);

function median(fn, runs = 5) {
  const ts = [];
  let out;
  for (let i = 0; i < runs; i++) {
    const t = performance.now();
    out = fn();
    ts.push(performance.now() - t);
  }
  ts.sort((a, b) => a - b);
  return { ms: ts[Math.floor(ts.length / 2)], out };
}

const bg = median(() => JSON.parse(build_graph(mJson, eJson, rJson)));
const graphJson = JSON.stringify(bg.out);
console.log(`build_graph: ${bg.out.entities.length} entities, ${bg.out.edges.length} edges — ${bg.ms.toFixed(1)} ms`);

const nb = median(() => JSON.parse(neighborhood(graphJson, JSON.stringify([0]), 3)));
console.log(`neighborhood(0, 3hops): ${nb.out.entities.length} entities — ${nb.ms.toFixed(1)} ms`);

const cm = median(() => JSON.parse(communities(graphJson)));
console.log(`communities: ${cm.out.length} — ${cm.ms.toFixed(1)} ms`);

const ok = bg.out.entities.length === N && cm.out.length >= 1;
console.log(ok ? `\n>>> PASS: ${N} entities built + queried, no large-array crash` : `\n>>> FAIL`);
process.exit(ok ? 0 : 1);
