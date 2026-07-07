/**
 * loader.ts — universal WASM byte loader + instantiation for the goldenpipe
 * planner kernel. Edge-safe: the only node:* touch is inside the shared
 * runtime's resolveWasmBytes. Resolution order (delegated): explicit bytes ->
 * base64 -> URL -> fs (Node) -> fetch. Any failure throws; index.ts turns
 * that into the pure-TS fallback (or rethrows under { require: true }).
 */
import {
  resolveWasmBytes as sharedResolveWasmBytes,
  type LoadOptions,
} from "goldenmatch-wasm-runtime";
import type { PipeWasmBackend } from "./backend.js";

export type { LoadOptions };

export function resolveWasmBytes(opts: LoadOptions): Promise<Uint8Array> {
  return sharedResolveWasmBytes(
    opts,
    new URL("./artifacts/goldenpipe_wasm_bg.wasm", import.meta.url),
  );
}

export async function instantiateBackend(bytes: Uint8Array): Promise<PipeWasmBackend> {
  const glue = (await import("./artifacts/goldenpipe_wasm.js" as string)) as {
    default: (input: { module_or_path: Uint8Array }) => Promise<unknown>;
    resolve_json: (s: string) => string;
    apply_decision_json: (s: string) => string;
    evaluate_builtin_json: (s: string) => string;
    auto_config_json: (s: string) => string;
    skip_if_falsy_json: (s: string) => string;
    plan_pipeline_json: (s: string) => string;
    apply_scale_hints_json: (s: string) => string;
    band_of_json: (s: string) => string;
  };
  await glue.default({ module_or_path: bytes });

  return {
    resolveJson: (s) => glue.resolve_json(s),
    applyDecisionJson: (s) => glue.apply_decision_json(s),
    evaluateBuiltinJson: (s) => glue.evaluate_builtin_json(s),
    autoConfigJson: (s) => glue.auto_config_json(s),
    skipIfFalsyJson: (s) => glue.skip_if_falsy_json(s),
    planPipelineJson: (s) => glue.plan_pipeline_json(s),
    applyScaleHintsJson: (s) => glue.apply_scale_hints_json(s),
    bandOfJson: (s) => glue.band_of_json(s),
  };
}
