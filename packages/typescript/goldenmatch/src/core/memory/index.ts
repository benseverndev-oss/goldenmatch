/**
 * memory/index.ts -- Barrel export for the Learning Memory module.
 *
 * Re-exports the public surface of `core/memory/*`. Caller-facing config
 * types (`MemoryConfig`, `LearningConfig`) intentionally remain re-exported
 * only from `core/types.ts` to avoid duplicate-name collisions when
 * `core/index.ts` star-exports both.
 */

export * from "./types.js";
export * from "./store.js";
export * from "./corrections.js";
export * from "./learner.js";
export * from "./hash.js";
