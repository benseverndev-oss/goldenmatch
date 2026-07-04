/**
 * graphComponents.ts — the ONE shared connected-components primitive for the
 * edge pipeline. Rust-source-of-truth: when the opt-in graph wasm backend is
 * enabled it runs the shared `graph-core` kernel (the same CC the Python native
 * path and the DuckDB/Postgres native UDFs use); otherwise a pure-TS union-find
 * is the faithful fallback. Callers that used to hand-roll their own union-find
 * (`ann-blocker`, `graph-er`, and the clustering step) route here instead, so
 * there is exactly one clustering implementation to keep correct.
 *
 * Edge-safe: no `node:` imports; pulls ZERO wasm bytes (the backend is reached
 * through the lean `graphWasmBackend` registry, `import type` only for the
 * heavy loader).
 */
import { getGraphWasmBackend } from "./graphWasmBackend.js";

/**
 * Connected components of the graph with vertices `allIds` and edges `pairs`
 * (endpoint pairs; any score/weight is irrelevant to CC). Every id in `allIds`
 * appears in exactly one component (singletons included).
 *
 * Returns member lists in a CANONICAL order — members ascending, components
 * ordered by their minimum member. The CC partition is unique, so this is
 * identical whether or not the wasm backend is enabled, AND it matches the
 * historical ascending-index-scan grouping the call sites used (their local
 * union-finds iterated `0..n` and grouped by root, which yields exactly this
 * order). So rerouting is output-preserving with the wasm backend off and
 * toggle-invariant with it on.
 */
export function connectedComponents(
  pairs: readonly (readonly [number, number])[],
  allIds: readonly number[],
): number[][] {
  const backend = getGraphWasmBackend();
  const comps = backend
    ? backend.connectedComponents(pairs, allIds)
    : unionFindComponents(pairs, allIds);

  for (const c of comps) c.sort((a, b) => a - b);
  comps.sort((a, b) => (a[0] ?? 0) - (b[0] ?? 0));
  return comps;
}

/** Pure-TS union-find with path compression + union by size. The faithful
 *  fallback when the wasm backend is not enabled. */
function unionFindComponents(
  pairs: readonly (readonly [number, number])[],
  allIds: readonly number[],
): number[][] {
  const parent = new Map<number, number>();
  const size = new Map<number, number>();

  const add = (x: number): void => {
    if (!parent.has(x)) {
      parent.set(x, x);
      size.set(x, 1);
    }
  };

  const find = (x: number): number => {
    add(x);
    let root = x;
    while (parent.get(root)! !== root) root = parent.get(root)!;
    // Path compression.
    let cur = x;
    while (parent.get(cur)! !== root) {
      const next = parent.get(cur)!;
      parent.set(cur, root);
      cur = next;
    }
    return root;
  };

  const union = (a: number, b: number): void => {
    const ra = find(a);
    const rb = find(b);
    if (ra === rb) return;
    if (size.get(ra)! < size.get(rb)!) {
      parent.set(ra, rb);
      size.set(rb, size.get(rb)! + size.get(ra)!);
    } else {
      parent.set(rb, ra);
      size.set(ra, size.get(ra)! + size.get(rb)!);
    }
  };

  for (const id of allIds) add(id);
  for (const [a, b] of pairs) union(a, b);

  const groups = new Map<number, number[]>();
  for (const id of allIds) {
    const root = find(id);
    const list = groups.get(root);
    if (list) list.push(id);
    else groups.set(root, [id]);
  }
  return [...groups.values()];
}
