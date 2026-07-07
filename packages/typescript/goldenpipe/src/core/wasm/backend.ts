/**
 * backend.ts — opt-in WASM planner-kernel backend registry. Edge-safe: no
 * node:* here. The active backend (if any) is consulted by the five planner
 * seams (Resolver.resolve, Router.apply, the decision gates, autoConfig, the
 * runner's isFalsy) via getPipeWasmBackend(); everything else stays pure-TS.
 * Mirrors goldenflow's setFlowWasmBackend module-singleton for test isolation.
 */

/**
 * A WASM-backed planner kernel over goldenpipe-core's five JSON wrappers
 * (see goldenpipe-wasm/src/lib.rs). Byte-identical to the Python/native
 * kernels by construction — a thin wasm-bindgen shim over the SAME
 * goldenpipe-core::json module. All five are string -> string.
 */
export interface PipeWasmBackend {
  resolveJson(input: string): string;
  applyDecisionJson(input: string): string;
  evaluateBuiltinJson(input: string): string;
  autoConfigJson(input: string): string;
  skipIfFalsyJson(input: string): string;
  planPipelineJson(input: string): string;
  applyScaleHintsJson(input: string): string;
  bandOfJson(input: string): string;
}

import { createBackendRegistry } from "goldenmatch-wasm-runtime";

const _registry = createBackendRegistry<PipeWasmBackend>();

export function setPipeWasmBackend(b: PipeWasmBackend | null): void {
  _registry.set(b);
}

export function getPipeWasmBackend(): PipeWasmBackend | null {
  return _registry.get();
}
