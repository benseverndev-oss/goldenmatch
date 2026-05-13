/**
 * Identity Graph -- public surface (edge-safe).
 *
 * Persistent (Node-only) SQLite backend lives at `src/node/identity/`. Both
 * implementations satisfy the same `IdentityStore` interface so consumers
 * stay backend-agnostic.
 */

export * from "./types.js";
export { newEntityId } from "./new-entity-id.js";
export { InMemoryIdentityStore } from "./in-memory-store.js";
export {
  findByRecord,
  getEntity,
  listEntities,
  manualMerge,
  manualSplit,
  type IdentityView,
} from "./query.js";
