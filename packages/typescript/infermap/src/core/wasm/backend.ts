/**
 * backend.ts — opt-in WASM detect backend registry. Edge-safe: no node:* here.
 * Mirrors goldenanalysis's wasm/backend.ts (module-singleton registry).
 */
import { createBackendRegistry } from "goldenmatch-wasm-runtime";
import type { DetectionResult } from "goldencheck-types";

/** A WASM-backed detect kernel. Dictionary resolution stays host; this scores a
 *  resolved [name, hints[]] domain list. */
export interface InfermapBackend {
  detectDomain(
    columns: string[],
    domains: Array<[string, string[]]>,
    minScore: number,
  ): DetectionResult;
}

const _registry = createBackendRegistry<InfermapBackend>();

export function setInfermapBackend(b: InfermapBackend | null): void {
  _registry.set(b);
}

export function getInfermapBackend(): InfermapBackend | null {
  return _registry.get();
}
