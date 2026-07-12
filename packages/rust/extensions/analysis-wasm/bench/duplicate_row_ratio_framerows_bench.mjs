// Confirm the win survives the REAL data shape: row-oriented, heterogeneous
// FrameRows (Array<Record<string,unknown>>), not pre-columnar typed arrays.
// A = the actual shipping pure-TS duplicateRowRatio (rowKey computed 2x/row).
// B = productionized wasm path: per-column canonical-cell-key intern -> int
//     columns -> wasm integer row-dedup.
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const wasm = require(new URL("../pkg-node/goldenmatch_analysis_wasm.js", import.meta.url));

function mulberry32(a) { return function () { a |= 0; a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }

// Build FrameRows: Array of {name, id, score} objects (~30% duplicate rows).
function makeRows(n, seed = 1) {
  const rnd = mulberry32(seed);
  const D = Math.max(1, Math.floor(n * 0.7));
  const pool = [];
  for (let i = 0; i < D; i++) pool.push({ name: "name_" + Math.floor(rnd() * 1e6).toString(36), id: Math.floor(rnd() * 1e7), score: Math.floor(rnd() * 1000) / 10 });
  const rows = new Array(n);
  for (let i = 0; i < n; i++) rows[i] = pool[Math.floor(rnd() * D)];
  return rows;
}

const isNullish = (v) => v === null || v === undefined;
const cellKey = (v) => (isNullish(v) ? " null" : JSON.stringify(v));

// A: shipping pure-TS duplicateRowRatio (faithful: rowKey 2x/row).
function rowKey(row, cols) { return JSON.stringify(cols.map((c) => cellKey(row[c]))); }
function A(rows, cols) {
  const n = rows.length, counts = new Map();
  for (const row of rows) { const k = rowKey(row, cols); counts.set(k, (counts.get(k) ?? 0) + 1); }
  let dup = 0;
  for (const row of rows) if ((counts.get(rowKey(row, cols)) ?? 0) > 1) dup += 1;
  return dup / n;
}

// B: productionized wasm path. Per column: cell -> canonical key -> intern (Map)
// -> BigInt64Array of ids; then wasm integer row-dedup.
function internColumn(rows, col) {
  const n = rows.length, map = new Map(), ids = new BigInt64Array(n);
  let next = 1n;
  for (let i = 0; i < n; i++) {
    const k = cellKey(rows[i][col]);
    let id = map.get(k);
    if (id === undefined) { id = next++; map.set(k, id); }
    ids[i] = id;
  }
  return ids;
}
function B(rows, cols, split) {
  const t0 = performance.now();
  const n = rows.length, valid = new Uint8Array(n).fill(1);
  const cols_ids = cols.map((c) => internColumn(rows, c));
  const tIntern = performance.now();
  const fi = new wasm.FrameInterner(n);
  for (const ids of cols_ids) fi.push_i64(ids, valid);
  const r = fi.duplicate_row_ratio();
  fi.free();
  const tCall = performance.now();
  if (split) { split.intern = tIntern - t0; split.call = tCall - tIntern; }
  return r;
}

function median(xs) { const s = [...xs].sort((a, b) => a - b); return s[s.length >> 1]; }
function timeIt(fn, runs = 5) { const ts = []; let o; for (let i = 0; i < runs; i++) { const a = performance.now(); o = fn(); ts.push(performance.now() - a); } return { ms: median(ts), out: o }; }

for (const n of [100_000, 500_000, 1_000_000]) {
  const rows = makeRows(n);
  const cols = ["name", "id", "score"];
  A(rows, cols); B(rows, cols, {}); // warm
  const a = timeIt(() => A(rows, cols));
  const sb = {};
  const b = timeIt(() => B(rows, cols, sb));
  const agree = Math.abs(a.out - b.out) < 1e-9;
  const sp = a.ms / b.ms;
  console.log(`n=${n.toLocaleString().padStart(9)} | pure-TS ${a.ms.toFixed(0).padStart(6)}ms | wasm-path ${b.ms.toFixed(0).padStart(6)}ms [intern ${sb.intern.toFixed(0)} + wasm ${sb.call.toFixed(0)}] | ${sp >= 1 ? sp.toFixed(2) + "x FASTER" : (1 / sp).toFixed(2) + "x slower"} | agree=${agree} (dup=${a.out.toFixed(4)})`);
}
