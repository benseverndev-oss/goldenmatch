/**
 * loader.ts — universal WASM byte loader + instantiation. Edge-safe: the only
 * node:* touch is a guarded dynamic `import("node:fs/promises" as string)`, the
 * documented idiom that keeps tsup from statically resolving node built-ins.
 *
 * Resolution order: explicit bytes → explicit URL → fs (Node) → fetch
 * (browser/Workers/bundler). Any failure throws; index.ts turns that into the
 * pure-TS fallback (or rethrows under { require: true }).
 */
import {
  resolveWasmBytes as sharedResolveWasmBytes,
  type LoadOptions,
} from "goldenmatch-wasm-runtime";
import { SCORER_ID } from "./backend.js";
import type { ScorerBackend } from "./backend.js";

export type { LoadOptions };

/**
 * Resolve the raw wasm bytes, pinning goldenmatch's artifact URL (computed here
 * so `import.meta.url` resolves to this package's own dist). `enableWasm` now
 * resolves via `enableWasmBackend`; this thin wrapper is kept for direct
 * callers/tests.
 */
export function resolveWasmBytes(opts: LoadOptions): Promise<Uint8Array> {
  return sharedResolveWasmBytes(
    opts,
    new URL("./artifacts/score_wasm_bg.wasm", import.meta.url),
  );
}

/**
 * Instantiate the score-wasm module and adapt it to a ScorerBackend. Uses the
 * wasm-bindgen `--target web` glue: the default export is the async `init`,
 * which accepts `{ module_or_path: <bytes|url|module> }`.
 */
export async function instantiateBackend(bytes: Uint8Array): Promise<ScorerBackend> {
  // Dynamic import of the generated glue (absent in a default checkout).
  const glue = (await import("./artifacts/score_wasm.js" as string)) as {
    // module_or_path accepts more (URL/Response/Module), but we only ever pass
    // the resolved bytes; typing it as Uint8Array avoids the DOM `BufferSource`
    // lib type (this package typechecks without the DOM lib).
    default: (input: { module_or_path: Uint8Array }) => Promise<unknown>;
    score_matrix: (values: string, sep: string, scorerId: number) => Float64Array;
  };
  await glue.default({ module_or_path: bytes });

  const SEP = "\x1e";
  return {
    scoreMatrix(values: readonly string[], scorerName: string): Float64Array {
      // SCORER_ID is the single source of truth (shared with the backend
      // registry + the Rust score_one discriminant) — never re-literal it here.
      // Note: id 2 (token_sort) is intentionally absent (deferred, see backend.ts).
      const id = SCORER_ID[scorerName];
      if (id === undefined) throw new Error(`uncovered scorer: ${scorerName}`);
      return glue.score_matrix(values.join(SEP), SEP, id);
    },
  };
}
