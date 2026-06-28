/**
 * goldenprofile — the edge-safe TypeScript surface for the GoldenProfile
 * Virtual Fingerprint engine (cross-document entity resolution).
 *
 * This base entry is pure types + the resolver + the backend registry. It pulls
 * ZERO wasm bytes. To actually resolve, opt into the kernel:
 *
 *   import { enableGoldenprofileWasm } from "goldenprofile/wasm";
 *   import { resolveProfiles } from "goldenprofile";
 *   enableGoldenprofileWasm();
 *   const result = resolveProfiles({ profiles: [...] });
 *
 * The resolver is the SAME pyo3-free Rust kernel (`goldenprofile-core`) the
 * Python (`goldenprofile_native`) and C bindings use — byte-identical clusters
 * by construction.
 */
export {
  setGoldenprofileWasmBackend,
  getGoldenprofileWasmBackend,
  disableGoldenprofileWasm,
  isGoldenprofileWasmEnabled,
  type GoldenprofileWasmBackend,
} from "./core/goldenprofileWasmBackend.js";

import { getGoldenprofileWasmBackend } from "./core/goldenprofileWasmBackend.js";

/** A profile element: a node (entity mention) or an edge (relationship mention). */
export type ElementKind = "node" | "edge";

/** One cross-document mention to resolve. Mirrors the Rust `Profile` serde shape. */
export interface Profile {
  kind: ElementKind;
  name: string;
  category: string;
  anchor: string;
  attribute: string;
}

/** The resolve input. `embeddings` are intentionally omitted — the structured +
 * SimHash path is self-contained and edge-safe (no model). */
export interface ResolveRequest {
  profiles: Profile[];
}

/** The full per-pair score breakdown — the audit trail behind every merge.
 * Mirrors the Rust `PairScore` serde shape exactly. */
export interface PairScore {
  name: number;
  category: number;
  anchor: number;
  embedding: number;
  attribute_bonus: number;
  /** `true` iff both hard gates passed; when `false`, `score === 0`. */
  gated_in: boolean;
  score: number;
}

/** A kept (merged) profile pair: indices into the request's `profiles`. */
export interface ResolvedEdge {
  a: number;
  b: number;
  score: PairScore;
}

/** The resolution result. `clusters` partitions EVERY profile index (singletons
 * included) into cross-document entities; `edges` are the scored merges. */
export interface Resolution {
  clusters: number[][];
  edges: ResolvedEdge[];
}

/**
 * Resolve `profiles` into cross-document entities.
 *
 * Requires the opt-in wasm backend — call `enableGoldenprofileWasm()` from
 * `goldenprofile/wasm` first. Without it this THROWS (it does not return an
 * empty/fake resolution, which would be silently wrong) — the exact analog of
 * the Python surface raising when `goldenprofile_native` is absent.
 */
export function resolveProfiles(request: ResolveRequest): Resolution {
  const backend = getGoldenprofileWasmBackend();
  if (backend === null) {
    throw new Error(
      "GoldenProfile resolution requires the wasm backend. " +
        'Import { enableGoldenprofileWasm } from "goldenprofile/wasm" and ' +
        "call it once before resolveProfiles().",
    );
  }
  return JSON.parse(backend.resolveJson(JSON.stringify(request))) as Resolution;
}
