/**
 * compose.ts — the goldenprofile → goldengraph bridge.
 *
 * GoldenProfile resolves mentions into a cluster PARTITION
 * (`Resolution.clusters`: `number[][]`, where `clusters[c]` lists the profile
 * indices in entity `c`). GoldenGraph's `buildGraph` wants the same information
 * the OTHER way round — a `{ mentionIndex: entityId }` map. These helpers convert
 * between the two so the two engines pipeline cleanly:
 *
 *   import { resolveProfiles } from "goldenprofile";
 *   import { enableGoldenprofileWasm } from "goldenprofile/wasm";
 *   import { buildGraph, resolutionFromClusters, mentionsFromProfiles } from "goldengraph";
 *   import { enableGoldengraphWasm } from "goldengraph/wasm";
 *
 *   enableGoldenprofileWasm(); enableGoldengraphWasm();
 *   const { clusters } = resolveProfiles({ profiles });
 *   const graph = buildGraph(
 *     mentionsFromProfiles(profiles),     // same order you resolved
 *     edges,                              // your mention-level edges
 *     resolutionFromClusters(clusters),   // the bridge
 *   );
 *
 * Zero-dep: these operate on plain shapes, so `goldengraph` does NOT depend on
 * `goldenprofile` — the caller already has both. The conversion is exact and
 * order-preserving (profile index i == mention index i), provided you build the
 * mentions list from the SAME profile list you resolved.
 */
import type { Mention, Resolution } from "./index.js";

/**
 * Convert a GoldenProfile cluster partition (`Resolution.clusters`) into a
 * GoldenGraph resolution map: each profile index -> its cluster index (the
 * entity id). The cluster's positional index becomes the `EntityId`.
 */
export function resolutionFromClusters(clusters: readonly number[][]): Resolution {
  const res: Record<number, number> = {};
  clusters.forEach((members, entityId) => {
    for (const idx of members) res[idx] = entityId;
  });
  return res;
}

/** The minimal profile shape this bridge reads (structurally GoldenProfile's `Profile`). */
export interface ProfileLike {
  name: string;
  category: string;
}

/**
 * Build the GoldenGraph `Mention[]` from GoldenProfile-style profiles, in the
 * SAME order (so indices line up with `resolutionFromClusters`). By default the
 * mention `typ` is the profile `category`; pass `typeOf` to override (e.g. to
 * derive the type from a different field).
 *
 * Note: this maps node-style entity mentions. GoldenProfile `edge`-kind profiles
 * are a separate concern — supply your graph's relationships as `buildGraph`'s
 * `edges` argument, not through this helper.
 */
export function mentionsFromProfiles<T extends ProfileLike>(
  profiles: readonly T[],
  typeOf: (profile: T) => string = (p) => p.category,
): Mention[] {
  return profiles.map((p) => ({ name: p.name, typ: typeOf(p) }));
}
