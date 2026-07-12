// Boundary-cost bench: wasm frame duplicate_row_ratio (via the minimal C-Data
// column-handle ABI) vs the pure-JS string-key dedup (the aggregate.ts analogue).
// Given the SAME columnar source, which dedups a frame faster end-to-end -- and
// does the JS->wasm buffer-build cost eat the win?
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const wasm = require(new URL("../pkg-node/goldenmatch_analysis_wasm.js", import.meta.url));

const enc = new TextEncoder();

// --- deterministic PRNG (no Math.random) ---
function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Build a frame of N rows sampled (with replacement) from D distinct tuples so
// ~ (N-D)/N of rows are duplicates. 3 columns: name(str), id(i64), score(f64).
function makeFrame(n, seed = 1) {
  const rnd = mulberry32(seed);
  const D = Math.max(1, Math.floor(n * 0.7)); // ~30% duplicate rows
  const poolNames = [], poolIds = [], poolScores = [];
  for (let i = 0; i < D; i++) {
    poolNames.push("name_" + Math.floor(rnd() * 1e6).toString(36));
    poolIds.push(BigInt(Math.floor(rnd() * 1e7)));
    poolScores.push(Math.floor(rnd() * 1000) / 10);
  }
  const names = new Array(n);
  const ids = new BigInt64Array(n);
  const scores = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const j = Math.floor(rnd() * D);
    names[i] = poolNames[j];
    ids[i] = poolIds[j];
    scores[i] = poolScores[j];
  }
  return { n, names, ids, scores };
}

// --- pure-JS dedup: one string key per row (null-free here) ---
function jsDuplicateRowRatio(f) {
  const counts = new Map();
  for (let i = 0; i < f.n; i++) {
    const key = f.names[i] + "\x1f" + f.ids[i] + "\x1f" + f.scores[i];
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  let dup = 0;
  for (const c of counts.values()) if (c >= 2) dup += c;
  return dup / f.n;
}

// --- wasm dedup: build the C-Data buffers, then intern+dedup in wasm ---
function encodeStrColumn(names) {
  const n = names.length;
  const offsets = new Uint32Array(n + 1);
  const parts = new Array(n);
  let total = 0;
  for (let i = 0; i < n; i++) {
    const b = enc.encode(names[i]);
    parts[i] = b;
    offsets[i] = total;
    total += b.length;
  }
  offsets[n] = total;
  const bytes = new Uint8Array(total);
  let o = 0;
  for (let i = 0; i < n; i++) { bytes.set(parts[i], o); o += parts[i].length; }
  return { offsets, bytes };
}

function wasmDuplicateRowRatio(f, split) {
  const t0 = performance.now();
  const valid = new Uint8Array(f.n).fill(1); // no nulls in this bench
  const { offsets, bytes } = encodeStrColumn(f.names);
  const tBuild = performance.now();
  const fi = new wasm.FrameInterner(f.n);
  fi.push_str(offsets, bytes, valid);
  fi.push_i64(f.ids, valid);
  fi.push_f64(f.scores, valid);
  const r = fi.duplicate_row_ratio();
  fi.free();
  const tCall = performance.now();
  if (split) { split.build = tBuild - t0; split.call = tCall - tBuild; }
  return r;
}

function median(xs) { const s = [...xs].sort((a, b) => a - b); return s[s.length >> 1]; }

function timeIt(fn, runs = 5) {
  const ts = [];
  let out;
  for (let i = 0; i < runs; i++) { const a = performance.now(); out = fn(); ts.push(performance.now() - a); }
  return { ms: median(ts), out };
}

// numeric-only variants (isolate string-marshaling cost)
function jsDupNumeric(f) {
  const counts = new Map();
  for (let i = 0; i < f.n; i++) {
    const key = f.ids[i] + "\x1f" + f.scores[i];
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  let dup = 0;
  for (const c of counts.values()) if (c >= 2) dup += c;
  return dup / f.n;
}
function wasmDupNumeric(f, split) {
  const t0 = performance.now();
  const valid = new Uint8Array(f.n).fill(1);
  const tBuild = performance.now(); // no per-cell work; typed arrays already in hand
  const fi = new wasm.FrameInterner(f.n);
  fi.push_i64(f.ids, valid);
  fi.push_f64(f.scores, valid);
  const r = fi.duplicate_row_ratio();
  fi.free();
  const tCall = performance.now();
  if (split) { split.build = tBuild - t0; split.call = tCall - tBuild; }
  return r;
}

function row(n, label, js, ws, split) {
  const agree = Math.abs(js.out - ws.out) < 1e-9;
  const sp = js.ms / ws.ms;
  console.log(
    `n=${n.toLocaleString().padStart(9)} ${label.padEnd(13)} | ` +
    `JS ${js.ms.toFixed(1).padStart(7)}ms | ` +
    `WASM ${ws.ms.toFixed(1).padStart(7)}ms (build ${split.build.toFixed(1).padStart(7)} + call ${split.call.toFixed(1).padStart(6)}) | ` +
    `${sp >= 1 ? sp.toFixed(2) + "x FASTER" : (1 / sp).toFixed(2) + "x slower"} | agree=${agree}`
  );
}

for (const n of [100_000, 500_000, 1_000_000]) {
  const f = makeFrame(n);
  // warm up both paths (JIT + wasm memory growth) before timing
  jsDuplicateRowRatio(f); wasmDuplicateRowRatio(f, {}); jsDupNumeric(f); wasmDupNumeric(f, {});

  const sMix = {}, sNum = {};
  row(n, "str+i64+f64", timeIt(() => jsDuplicateRowRatio(f)), timeIt(() => wasmDuplicateRowRatio(f, sMix)), sMix);
  row(n, "i64+f64 only", timeIt(() => jsDupNumeric(f)), timeIt(() => wasmDupNumeric(f, sNum)), sNum);
}
