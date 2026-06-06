/**
 * Identity Graph -- public surface (edge-safe).
 *
 * Today only ``InMemoryIdentityStore`` is shipped. A persistent (Node-only)
 * SQLite backend is planned at ``src/node/identity/`` to satisfy the same
 * ``IdentityStore`` interface; that work is deferred to a future wave (see
 * CHANGELOG.md v0.8.0 "Not yet shipped"). Consumers should code against the
 * ``IdentityStore`` interface so they can swap implementations later.
 */

export * from "./types.js";
export { newEntityId } from "./new-entity-id.js";
export { InMemoryIdentityStore } from "./in-memory-store.js";
export {
  resolveClusters,
  type ResolveSummary,
  type ResolveOptions,
} from "./resolve.js";
export {
  findByRecord,
  findConflicts,
  getEntity,
  history,
  listEntities,
  manualMerge,
  manualSplit,
  type IdentityView,
} from "./query.js";
