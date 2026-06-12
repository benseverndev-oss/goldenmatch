// 5-run median wall: pure-TS vs WASM scoreMatrix on a realistic NxN block.
// Graduation gate: a core ships WASM acceleration only if WASM measurably wins.
// Run AFTER build_wasm.sh + `npm run build`. Usage: node scripts/bench_wasm_scorer.mjs [N]
import { scoreMatrix, enableWasm, disableWasm } from "../dist/core/index.js";

const N = Number(process.argv[2] ?? 1500);
const SCORER = "jaro_winkler";
const rnd = (seed) => () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);
const r = rnd(42);
const pick = "abcdefghijklmnopqrstuvwxyz ";
const mkStr = () => Array.from({ length: 6 + ((r() * 8) | 0) }, () => pick[(r() * pick.length) | 0]).join("");
const values = Array.from({ length: N }, mkStr);

const median = (xs) => xs.slice().sort((a, b) => a - b)[Math.floor(xs.length / 2)];
function time(fn) {
  const runs = [];
  for (let k = 0; k < 5; k++) {
    const t0 = performance.now();
    fn();
    runs.push(performance.now() - t0);
  }
  return median(runs);
}

disableWasm();
const pureMs = time(() => scoreMatrix(values, SCORER));
const ok = await enableWasm();
if (!ok) {
  console.error("WASM artifact not built — run score-wasm/build_wasm.sh first.");
  process.exit(1);
}
const wasmMs = time(() => scoreMatrix(values, SCORER));
disableWasm();

console.log(`N=${N} scorer=${SCORER}`);
console.log(`pure-TS : ${pureMs.toFixed(1)} ms (median of 5)`);
console.log(`WASM    : ${wasmMs.toFixed(1)} ms (median of 5)`);
console.log(`speedup : ${(pureMs / wasmMs).toFixed(2)}x`);
