/**
 * clusterWasm.ts — synchronous, edge-safe loader for the cluster-core
 * MST-split + confidence kernels, compiled to wasm.
 *
 * This is the SAME kernel the Python native path and the Rust core run, so the
 * oversized-cluster split + cluster-confidence math is identical across surfaces
 * (proven against the pure-TS impl in `tests/parity/cluster-wasm.parity.test.ts`
 * and against the Python `cluster-handoff.json` oracle). Importing this module
 * and calling `enableClusterWasm()` reroutes `splitOversizedCluster` /
 * `computeClusterConfidence` (cluster.ts) off their hand-written impls onto this
 * one core; the pure-TS stays the faithful fallback.
 *
 * Edge-safe: no `node:*`. The wasm is inlined as base64 and instantiated
 * synchronously via `initSync`. Row ids are small (0-based positions), so edge
 * endpoints cross as `Int32Array`s + a `Float64Array` of weights; the ragged
 * `number[][]` split result and the confidence tuple cross back as JSON.
 */
import {
  initSync,
  mst_split_components,
  cluster_confidence,
} from "./_wasm/clusterWasmBindings.js";
import { CLUSTER_WASM_BASE64 } from "./_wasm/clusterWasmBytes.js";
import {
  setClusterWasmBackend,
  disableClusterWasm,
  type ClusterConfidenceTuple,
} from "./clusterWasmBackend.js";

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
  initSync({ module: base64ToBytes(CLUSTER_WASM_BASE64) });
  initialized = true;
}

/** Split the edge triples into the three parallel typed arrays the kernel takes. */
function splitEdges(
  edges: readonly (readonly [number, number, number])[],
): { a: Int32Array; b: Int32Array; w: Float64Array } {
  const n = edges.length;
  const a = new Int32Array(n);
  const b = new Int32Array(n);
  const w = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    a[i] = edges[i]![0];
    b[i] = edges[i]![1];
    w[i] = edges[i]![2];
  }
  return { a, b, w };
}

/**
 * Max-weight spanning tree, drop the single weakest MST edge, return the
 * components — via the shared cluster-core kernel. Returns one member-id array
 * per component (order unspecified); `[]` when the MST is empty (unsplittable).
 */
export function mstSplitComponents(
  members: readonly number[],
  edges: readonly (readonly [number, number, number])[],
): number[][] {
  ensureInit();
  const { a, b, w } = splitEdges(edges);
  return JSON.parse(mst_split_components(Int32Array.from(members), a, b, w));
}

/**
 * Cluster confidence tuple via the shared cluster-core kernel.
 */
export function clusterConfidence(
  edges: readonly (readonly [number, number, number])[],
  size: number,
): ClusterConfidenceTuple {
  ensureInit();
  const { a, b, w } = splitEdges(edges);
  return JSON.parse(cluster_confidence(a, b, w, size)) as ClusterConfidenceTuple;
}

/**
 * Route `splitOversizedCluster` / `computeClusterConfidence` (cluster.ts) off
 * their pure-TS impls onto the shared cluster-core kernel. Idempotent. Call
 * `disableClusterWasm()` to revert (test isolation / opt-out).
 */
export function enableClusterWasm(): void {
  ensureInit();
  setClusterWasmBackend({ mstSplitComponents, clusterConfidence });
}

export { disableClusterWasm };
