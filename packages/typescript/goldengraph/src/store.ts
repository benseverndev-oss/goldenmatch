/**
 * store.ts — the GoldenGraph bitemporal store surface.
 *
 * Stateless ops over a portable JSON snapshot (the snapshot IS the state — no
 * handles cross the boundary). Append batches over time, then read the graph
 * "as of" a (valid-time, transaction-time) point, or an entity's merge/split
 * history. The kernel is the same `goldengraph-core` the Python/C bindings use.
 *
 *   import { appendBatch, asOf, history } from "goldengraph";
 *   import { enableGoldengraphWasm } from "goldengraph/wasm";
 *   enableGoldengraphWasm();
 *   let snap = appendBatch(null, batch1);   // null opens a fresh store
 *   snap = appendBatch(snap, batch2);       // chain
 *   const graph = asOf(snap, Date.now(), Date.now());
 */
import { requireGoldengraphWasmBackend } from "./core/goldengraphWasmBackend.js";
import type { Graph } from "./index.js";

/** An entity in an append batch (local id is batch-scoped). Mirrors `BatchEntity`. */
export interface BatchEntity {
  local_id: number;
  canonical_name: string;
  typ: string;
  surface_names: string[];
  /** Host-supplied stable keys (e.g. `:h1:` fingerprints) used to match across batches. */
  record_keys: string[];
}

/** A relationship in an append batch, over batch-local ids. Mirrors `BatchEdge`. */
export interface BatchEdge {
  subj_local: number;
  predicate: string;
  obj_local: number;
  valid_from: number;
  valid_to: number | null;
  source_refs: string[];
}

/** A batch of entities + edges ingested at `ingested_at` (transaction time). */
export interface StoreBatch {
  entities: BatchEntity[];
  edges: BatchEdge[];
  ingested_at: number;
}

/** A stored entity with bitemporal supersession metadata. Mirrors `StoredEntity`. */
export interface StoredEntity {
  id: number;
  canonical_name: string;
  typ: string;
  surface_names: string[];
  record_keys: string[];
  created_at: number;
  superseded_by: number | null;
  superseded_at: number | null;
}

/** A stored edge with a validity interval. Mirrors `StoredEdge`. */
export interface StoredEdge {
  subj: number;
  predicate: string;
  obj: number;
  valid_from: number;
  valid_to: number | null;
  ingested_at: number;
  source_refs: string[];
}

/** A merge/split event in an entity's history (externally-tagged, mirrors `HistoryEvent`). */
export type HistoryEvent =
  | { Merge: { kept: number; absorbed: number[]; at: number } }
  | { Split: { from: number; into: number[]; at: number } };

/** The portable bitemporal store snapshot (the serialized kernel `GraphStore`). */
export interface Snapshot {
  /** Keyed by stable id (stringified, since it's a JSON object). */
  entities: Record<string, StoredEntity>;
  edges: StoredEdge[];
  history: HistoryEvent[];
  next_id: number;
}

/**
 * Append a batch. Pass `null` (or an empty snapshot) to open a fresh store;
 * otherwise the batch is merged into `snapshot`. Returns the new snapshot —
 * chain calls to ingest over time.
 */
export function appendBatch(snapshot: Snapshot | null, batch: StoreBatch): Snapshot {
  const snapJson = snapshot === null ? "" : JSON.stringify(snapshot);
  return JSON.parse(
    requireGoldengraphWasmBackend().storeAppend(snapJson, JSON.stringify(batch)),
  ) as Snapshot;
}

/** The resolved graph as of `(validT, txT)` — a bitemporal slice of the snapshot. */
export function asOf(snapshot: Snapshot, validT: number, txT: number): Graph {
  return JSON.parse(
    requireGoldengraphWasmBackend().storeAsOf(JSON.stringify(snapshot), validT, txT),
  ) as Graph;
}

/** The merge/split history of entity `id` in the snapshot. */
export function history(snapshot: Snapshot, id: number): HistoryEvent[] {
  return JSON.parse(
    requireGoldengraphWasmBackend().storeHistory(JSON.stringify(snapshot), id),
  ) as HistoryEvent[];
}
