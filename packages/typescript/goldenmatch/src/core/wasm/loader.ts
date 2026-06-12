/**
 * loader.ts — universal WASM byte loader + instantiation. Edge-safe: the only
 * node:* touch is a guarded dynamic `import("node:fs/promises" as string)`, the
 * documented idiom that keeps tsup from statically resolving node built-ins.
 *
 * Resolution order: explicit bytes → explicit URL → fs (Node) → fetch
 * (browser/Workers/bundler). Any failure throws; index.ts turns that into the
 * pure-TS fallback (or rethrows under { require: true }).
 */
import { SCORER_ID } from "./backend.js";
import type { ScorerBackend } from "./backend.js";

export interface LoadOptions {
  readonly wasmBytes?: Uint8Array;
  readonly wasmUrl?: string | URL;
}

/** Resolve the raw wasm bytes for the current environment. */
export async function resolveWasmBytes(opts: LoadOptions): Promise<Uint8Array> {
  if (opts.wasmBytes !== undefined) {
    if (opts.wasmBytes.byteLength === 0) throw new Error("empty wasmBytes");
    return opts.wasmBytes;
  }
  const url =
    opts.wasmUrl ?? new URL("./artifacts/score_wasm_bg.wasm", import.meta.url);

  const isNode =
    typeof process !== "undefined" &&
    process.versions?.node !== undefined &&
    (url instanceof URL ? url.protocol === "file:" : String(url).startsWith("file:"));

  if (isNode) {
    const fs = await import("node:fs/promises" as string);
    const buf = await fs.readFile(url as URL);
    return new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
  }
  const resp = await fetch(url as URL);
  if (!resp.ok) throw new Error(`fetch wasm failed: ${resp.status}`);
  return new Uint8Array(await resp.arrayBuffer());
}

/**
 * Instantiate the score-wasm module and adapt it to a ScorerBackend. Uses the
 * wasm-bindgen `--target web` glue: the default export is the async `init`,
 * which accepts `{ module_or_path: <bytes|url|module> }`.
 */
export async function instantiateBackend(bytes: Uint8Array): Promise<ScorerBackend> {
  // Dynamic import of the generated glue (absent in a default checkout).
  const glue = (await import("./artifacts/score_wasm.js" as string)) as {
    default: (input: { module_or_path: BufferSource }) => Promise<unknown>;
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
