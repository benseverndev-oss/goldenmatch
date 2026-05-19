/**
 * Node-only Identity Graph backend.
 *
 * SqliteIdentityStore is the persistent implementation; consumers running
 * on Vercel Edge or Cloudflare Workers should use InMemoryIdentityStore
 * from src/core/identity instead.
 */
export {
  SqliteIdentityStore,
  type SqliteIdentityStoreOptions,
} from "./sqlite-store.js";
