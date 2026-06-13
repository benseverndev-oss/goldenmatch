/**
 * loader.ts — instantiate the analysis-wasm module and adapt it to an
 * AnalysisBackend. The wasm-bindgen glue import is dynamic (absent in a default
 * checkout). Byte resolution + env detection live in the shared runtime (driven
 * from index.ts via enableWasmBackend); this module only does the glue + adapt.
 */
import type { AnalysisBackend } from "./backend.js";

export async function instantiateBackend(bytes: Uint8Array): Promise<AnalysisBackend> {
  const glue = (await import("./artifacts/analysis_wasm.js" as string)) as {
    default: (input: { module_or_path: Uint8Array }) => Promise<unknown>;
    histogram: (values: Float64Array, bins: number) => Float64Array;
    quantile: (values: Float64Array, q: number) => number;
  };
  await glue.default({ module_or_path: bytes });
  return {
    histogram(values, bins) {
      // analysis-wasm returns the histogram flattened as [edge,count,edge,...].
      const flat = glue.histogram(values, bins);
      const out: Array<[number, number]> = [];
      for (let i = 0; i + 1 < flat.length; i += 2) {
        out.push([flat[i]!, flat[i + 1]!]);
      }
      return out;
    },
    quantile(values, q) {
      return glue.quantile(values, q);
    },
  };
}
