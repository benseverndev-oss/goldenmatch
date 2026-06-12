// 5-run median wall: pure-TS vs WASM histogram/quantile on a realistic large
// column. Graduation gate: ship acceleration only if WASM measurably wins.
// Run AFTER analysis-wasm/build_wasm.sh + `npm run build`.
// Usage: node scripts/bench_wasm_aggregate.mjs [N]
import {
  aggregate,
  enableAnalysisWasm,
  disableAnalysisWasm,
} from "../dist/core/index.js";

const N = Number(process.argv[2] ?? 1_000_000);
const BINS = 256;
const Q = 0.9;

// Deterministic LCG values in a realistic range.
let s = 42 >>> 0;
const rnd = () => ((s = (s * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);
const values = Array.from({ length: N }, () => rnd() * 1000 - 500);

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

disableAnalysisWasm();
const pureHist = time(() => aggregate.histogram(values, BINS));
const pureQuant = time(() => aggregate.quantile(values, Q));

const ok = await enableAnalysisWasm();
if (!ok) {
  console.error("WASM artifact not built — run analysis-wasm/build_wasm.sh first.");
  process.exit(1);
}
const wasmHist = time(() => aggregate.histogram(values, BINS));
const wasmQuant = time(() => aggregate.quantile(values, Q));
disableAnalysisWasm();

console.log(`N=${N}`);
console.log(`histogram(${BINS} bins)  pure-TS ${pureHist.toFixed(1)} ms | WASM ${wasmHist.toFixed(1)} ms | speedup ${(pureHist / wasmHist).toFixed(2)}x`);
console.log(`quantile(q=${Q})        pure-TS ${pureQuant.toFixed(1)} ms | WASM ${wasmQuant.toFixed(1)} ms | speedup ${(pureQuant / wasmQuant).toFixed(2)}x`);
