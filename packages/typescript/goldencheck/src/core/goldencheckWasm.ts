/**
 * goldencheckWasm.ts — synchronous, edge-safe loader for the goldencheck-core
 * deep-profiling kernels (composite-key / functional-dependency mining, Benford,
 * near-duplicate clustering), compiled to wasm.
 *
 * This is the SAME code the Python `goldencheck-native` wheel and the Rust core
 * run, so the outputs are byte-identical across Python / Rust / TS — proven by
 * the shared golden vector (`tests/parity/goldencheck-wasm.parity.test.ts` reads
 * the fixture generated from the kernel; `goldencheck-core/tests/golden.rs`
 * checks the same one). Importing this module and calling
 * `enableGoldencheckWasm()` reroutes the relations off their hand-written TS
 * re-implementations onto this one core.
 *
 * Edge-safe: no `node:*`. The wasm is inlined as base64 and instantiated
 * synchronously via wasm-bindgen's `initSync` (browsers / Workers / Node). JSON
 * crosses the boundary (columnar inputs don't fit typed arrays cleanly).
 */
import {
  initSync,
  gc_composite_key_search,
  gc_discover_functional_dependencies,
  gc_discover_approximate_fds,
  gc_functional_dependency_holds,
  gc_fd_violation_rows,
  gc_benford_leading_digits,
  gc_near_duplicate_clusters,
} from "./_wasm/goldencheckWasmBindings.js";
import { GOLDENCHECK_WASM_BASE64 } from "./_wasm/goldencheckWasmBytes.js";
import {
  setGoldencheckWasmBackend,
  disableGoldencheckWasm,
  type WasmColumn,
} from "./goldencheckWasmBackend.js";

// ── one-time synchronous wasm init (edge-safe: atob, no fs/fetch) ────────────

let initialized = false;

function base64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64); // browsers, Workers, Node >= 18 — edge-safe
  const len = bin.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function ensureInit(): void {
  if (initialized) return;
  initSync({ module: base64ToBytes(GOLDENCHECK_WASM_BASE64) });
  initialized = true;
}

// ── typed wrappers (JSON boundary hidden) ────────────────────────────────────

/** Strict single-column FDs `(detIdx, depIdx)` among `columns`. */
export function discoverFunctionalDependencies(
  columns: readonly WasmColumn[],
): Array<[number, number]> {
  ensureInit();
  return JSON.parse(gc_discover_functional_dependencies(JSON.stringify(columns)));
}

/** Approximate FDs `(detIdx, depIdx, nViolations)` at `minConfidence`. */
export function discoverApproximateFds(
  columns: readonly WasmColumn[],
  minConfidence: number,
): Array<[number, number, number]> {
  ensureInit();
  return JSON.parse(gc_discover_approximate_fds(JSON.stringify(columns), minConfidence));
}

/** Whether `lhs -> rhs` holds. */
export function functionalDependencyHolds(lhs: WasmColumn, rhs: WasmColumn): boolean {
  ensureInit();
  return JSON.parse(
    gc_functional_dependency_holds(JSON.stringify(lhs), JSON.stringify(rhs)),
  );
}

/** Row indices where `dep` deviates from its per-`det`-group mode. */
export function fdViolationRows(det: WasmColumn, dep: WasmColumn): number[] {
  ensureInit();
  return JSON.parse(gc_fd_violation_rows(JSON.stringify(det), JSON.stringify(dep)));
}

/** Minimal composite keys among `columns` (subsets of column indices). */
export function compositeKeySearch(
  columns: readonly WasmColumn[],
  maxSize: number,
  singleUnique: readonly boolean[],
): number[][] {
  ensureInit();
  return JSON.parse(
    gc_composite_key_search(JSON.stringify(columns), maxSize, JSON.stringify(singleUnique)),
  );
}

/** Benford leading-digit histogram (9 bins) over the finite positive values. */
export function benfordLeadingDigits(values: readonly (number | null)[]): number[] {
  ensureInit();
  return JSON.parse(gc_benford_leading_digits(JSON.stringify(values)));
}

/** Cluster near-duplicate (edit-distance-close) values (indices into `values`). */
export function nearDuplicateClusters(
  values: readonly string[],
  minSimilarity: number,
): number[][] {
  ensureInit();
  return JSON.parse(
    gc_near_duplicate_clusters(JSON.stringify(values), minSimilarity),
  );
}

// ── opt-in enable / disable ──────────────────────────────────────────────────

/**
 * Route the goldencheck relations off their pure-TS re-implementations onto the
 * shared wasm core. Idempotent. Call `disableGoldencheckWasm()` to revert (test
 * isolation / opt-out).
 */
export function enableGoldencheckWasm(): void {
  ensureInit();
  setGoldencheckWasmBackend({
    compositeKeySearch,
    discoverFunctionalDependencies,
    nearDuplicateClusters,
  });
}

export { disableGoldencheckWasm };
