/**
 * goldengraph — the edge-safe TypeScript surface for the GoldenGraph
 * knowledge-graph engine: build a resolved entity graph from mentions, then
 * query it (neighborhoods, seeds-by-name, communities).
 *
 * The base entry is pure types + the query API + the backend registry. It pulls
 * ZERO wasm bytes. To run, opt into the kernel:
 *
 *   import { enableGoldengraphWasm } from "goldengraph/wasm";
 *   import { buildGraph, communities } from "goldengraph";
 *   enableGoldengraphWasm();
 *   const graph = buildGraph(mentions, edges, resolution);
 *
 * The engine is the SAME pyo3-free Rust kernel (`goldengraph-core`) the Python
 * (`goldengraph_native`) and C bindings use — byte-identical by construction.
 *
 * v1 surfaces the 4 graph+query ops. The bitemporal store
 * (`store_append/as_of/history`) is in the kernel but not yet wired here.
 */
export {
  setGoldengraphWasmBackend,
  getGoldengraphWasmBackend,
  disableGoldengraphWasm,
  isGoldengraphWasmEnabled,
  type GoldengraphWasmBackend,
} from "./core/goldengraphWasmBackend.js";

import { getGoldengraphWasmBackend } from "./core/goldengraphWasmBackend.js";

/** A raw mention to be resolved. Mirrors the Rust `Mention` serde shape. */
export interface Mention {
  name: string;
  typ: string;
}

/** A mention-level relationship (subject/predicate/object over mention indices). */
export interface MentionEdge {
  subj: number;
  predicate: string;
  obj: number;
  source_ref: string;
}

/** A resolved entity node. Mirrors the Rust `EntityNode` serde shape. */
export interface EntityNode {
  entity_id: number;
  canonical_name: string;
  typ: string;
  members: number[];
  surface_names: string[];
}

/** An entity-level edge. `source_refs` may be empty. */
export interface Edge {
  subj: number;
  predicate: string;
  obj: number;
  source_refs: string[];
}

/** The resolution-merged knowledge graph (and the shape of a neighborhood subgraph). */
export interface Graph {
  entities: EntityNode[];
  edges: Edge[];
}

/** A community: a positional `id` (sorted by min member) + its member entity ids. */
export interface Community {
  id: number;
  members: number[];
}

/**
 * How mentions resolve into entities:
 * - a `{ mentionIndex: entityId }` map (caller-provided resolution), or
 * - `["native", scorerId, threshold]` to resolve with the built-in scorer.
 */
export type Resolution =
  | Record<number, number>
  | ["native", number, number];

function backendOrThrow() {
  const backend = getGoldengraphWasmBackend();
  if (backend === null) {
    throw new Error(
      "GoldenGraph requires the wasm backend. " +
        'Import { enableGoldengraphWasm } from "goldengraph/wasm" and call it ' +
        "once before any query.",
    );
  }
  return backend;
}

/** Build a resolution-merged knowledge graph from mentions + edges. */
export function buildGraph(
  mentions: Mention[],
  edges: MentionEdge[],
  resolution: Resolution,
): Graph {
  const out = backendOrThrow().buildGraph(
    JSON.stringify(mentions),
    JSON.stringify(edges),
    JSON.stringify(resolution),
  );
  return JSON.parse(out) as Graph;
}

/** The `hops`-hop neighborhood subgraph around `seeds` (entity ids). */
export function neighborhood(graph: Graph, seeds: number[], hops: number): Graph {
  const out = backendOrThrow().neighborhood(
    JSON.stringify(graph),
    JSON.stringify(seeds),
    hops,
  );
  return JSON.parse(out) as Graph;
}

/** Entity ids whose surface names match `name`. */
export function seedsByName(graph: Graph, name: string): number[] {
  return JSON.parse(backendOrThrow().seedsByName(JSON.stringify(graph), name)) as number[];
}

/** Partition the graph's entities into communities (deterministic). */
export function communities(graph: Graph): Community[] {
  return JSON.parse(backendOrThrow().communities(JSON.stringify(graph))) as Community[];
}
