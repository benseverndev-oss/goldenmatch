/**
 * backend.ts — opt-in WASM detect backend registry. Edge-safe: no node:* here.
 * Mirrors goldenanalysis's wasm/backend.ts (module-singleton registry).
 */
import { createBackendRegistry } from "goldenmatch-wasm-runtime";
import type { DetectionResult } from "goldencheck-types";

/** A role flattened by the host for the layers kernel. `typical_type_hints` is
 *  the union of the role's `typical_types`' names and name_hints — resolved
 *  host-side so the kernel never learns about domain-pack YAML. */
export interface LayerRoleInput {
  name: string;
  kind: string;
  name_hints: string[];
  typical_type_hints: string[];
}

/** The kernel's flat layer record, BEFORE the host folds the evidence fields
 *  into `IdentityLayer.evidence`. Both the WASM and pure paths return this shape
 *  so a single host-side mapper produces the public result — otherwise the two
 *  paths could drift in how they assemble evidence. */
export interface RawLayer {
  role: string;
  kind: string;
  columns: string[];
  score: number;
  reason: string;
  qualifier: string;
  positions: string[];
  role_matched: boolean;
  type_corroboration: number;
}

export interface RawLayerDetection {
  layers: RawLayer[];
  unassigned: string[];
}

/** A WASM-backed detect kernel. Dictionary resolution stays host; this scores a
 *  resolved [name, hints[]] domain list. */
export interface InfermapBackend {
  detectDomain(
    columns: string[],
    domains: Array<[string, string[]]>,
    minScore: number,
  ): DetectionResult;
  detectIdentityLayers(
    columns: string[],
    roles: LayerRoleInput[],
    typeHints: string[],
    minScore: number,
  ): RawLayerDetection;
  exactScore(a: string, b: string): number;
  fuzzyNameScore(a: string, b: string): number;
  initialismScore(a: string, b: string): number | null;
  profileScore(
    srcDtype: string,
    tgtDtype: string,
    srcNull: number,
    tgtNull: number,
    srcUniq: number,
    tgtUniq: number,
    srcValCount: number,
    tgtValCount: number,
    srcAvgLen: number,
    tgtAvgLen: number,
  ): number;
  patternMatchTypes(samples: string[]): number[];
}

const _registry = createBackendRegistry<InfermapBackend>();

export function setInfermapBackend(b: InfermapBackend | null): void {
  _registry.set(b);
}

export function getInfermapBackend(): InfermapBackend | null {
  return _registry.get();
}
