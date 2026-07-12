// Option B: intern strings -> ints on the JS side (fast Map), pass only INTEGER
// columns to wasm (cheap boundary), let wasm do the row-tuple dedup natively.
// Compare vs A (baseline JS composite-string-key dedup) and C (JS interns then
// JS dedups -- "does wasm still earn its keep once strings are gone?").
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const wasm = require(new URL("../pkg-node/goldenmatch_analysis_wasm.js", import.meta.url));

function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function makeFrame(n, seed = 1) {
  const rnd = mulberry32(seed);
  const D = Math.max(1, Math.floor(n * 0.7));
  const pn = [], pi = [], ps = [];
  for (let i = 0; i < D; i++) {
    pn.push("name_" + Math.floor(rnd() * 1e6).toString(36));
    pi.push(BigInt(Math.floor(rnd() * 1e7)));
    ps.push(Math.floor(rnd() * 1000) / 10);
  }
  const names = new Array(n), ids = new BigInt64Array(n), scores = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const j = Math.floor(rnd() * D);
    names[i] = pn[j]; ids[i] = pi[j]; scores[i] = ps[j];
  }
  return { n, names, ids, scores };
}

// A: baseline -- one composite STRING key per row.
function A(f) {
  const m = new Map();
  for (let i = 0; i < f.n; i++) {
    const k = f.names[i] + "\x1f" + f.ids[i] + "\x1f" + f.scores[i];
    m.set(k, (m.get(k) || 0) + 1);
  }
  let d = 0; for (const c of m.values()) if (c >= 2) d += c; return d / f.n;
}

// intern a string column -> BigInt64Array of dense ids (JS Map; null-free here)
function internStr(names) {
  const map = new Map();
  const ids = new BigInt64Array(names.length);
  let next = 1n;
  for (let i = 0; i < names.length; i++) {
    let id = map.get(names[i]);
    if (id === undefined) { id = next++; map.set(names[i], id); }
    ids[i] = id;
  }
  return ids;
}

// B: JS interns the string column, wasm dedups the integer tuples.
function B(f, split) {
  const t0 = performance.now();
  const valid = new Uint8Array(f.n).fill(1);
  const nameIds = internStr(f.names);
  const tIntern = performance.now();
  const fi = new wasm.FrameInterner(f.n);
  fi.push_i64(nameIds, valid);
  fi.push_i64(f.ids, valid);
  fi.push_f64(f.scores, valid);
  const r = fi.duplicate_row_ratio();
  fi.free();
  const tCall = performance.now();
  if (split) { split.intern = tIntern - t0; split.call = tCall - tIntern; }
  return r;
}

// C: JS interns the string column, then JS dedups (composite key on ints).
function C(f, split) {
  const t0 = performance.now();
  const nameIds = internStr(f.names);
  const tIntern = performance.now();
  const m = new Map();
  for (let i = 0; i < f.n; i++) {
    const k = nameIds[i] + "\x1f" + f.ids[i] + "\x1f" + f.scores[i];
    m.set(k, (m.get(k) || 0) + 1);
  }
  let d = 0; for (const c of m.values()) if (c >= 2) d += c;
  const tDedup = performance.now();
  if (split) { split.intern = tIntern - t0; split.dedup = tDedup - tIntern; }
  return d / f.n;
}

function median(xs) { const s = [...xs].sort((a, b) => a - b); return s[s.length >> 1]; }
function timeIt(fn, runs = 5) { const ts = []; let o; for (let i = 0; i < runs; i++) { const a = performance.now(); o = fn(); ts.push(performance.now() - a); } return { ms: median(ts), out: o }; }

for (const n of [100_000, 500_000, 1_000_000]) {
  const f = makeFrame(n);
  A(f); B(f, {}); C(f, {}); // warm up
  const a = timeIt(() => A(f));
  const sb = {}, sc = {};
  const b = timeIt(() => B(f, sb));
  const c = timeIt(() => C(f, sc));
  const ok = Math.abs(a.out - b.out) < 1e-9 && Math.abs(a.out - c.out) < 1e-9;
  const vsA = a.ms / b.ms;
  console.log(
    `n=${n.toLocaleString().padStart(9)} | ` +
    `A(baseline) ${a.ms.toFixed(0).padStart(6)}ms | ` +
    `B(JS-intern+wasm) ${b.ms.toFixed(0).padStart(6)}ms [intern ${sb.intern.toFixed(0)} + wasm ${sb.call.toFixed(0)}] | ` +
    `C(JS-intern+JS) ${c.ms.toFixed(0).padStart(6)}ms | ` +
    `B vs A: ${vsA >= 1 ? vsA.toFixed(2) + "x FASTER" : (1 / vsA).toFixed(2) + "x slower"} | agree=${ok}`
  );
}
