/**
 * loader.ts — instantiate infermap-wasm and adapt it to an InfermapBackend.
 * The wasm-bindgen glue import is dynamic (absent in a default checkout).
 */
import type { DetectionResult } from "goldencheck-types";
import type { InfermapBackend } from "./backend.js";

export async function instantiateBackend(bytes: Uint8Array): Promise<InfermapBackend> {
  const glue = (await import("./artifacts/infermap_wasm.js" as string)) as {
    default: (input: { module_or_path: Uint8Array }) => Promise<unknown>;
    detect_domain_json: (input_json: string) => string;
  };
  await glue.default({ module_or_path: bytes });
  return {
    detectDomain(columns, domains, minScore) {
      // One JSON crossing per call (perf-audit lesson).
      const input = JSON.stringify({ columns, domains, min_score: minScore });
      return JSON.parse(glue.detect_domain_json(input)) as DetectionResult;
    },
  };
}
