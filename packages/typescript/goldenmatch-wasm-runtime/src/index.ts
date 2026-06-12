/**
 * goldenmatch-wasm-runtime — shared opt-in WASM plumbing for the Golden Suite
 * TS packages. Edge-safe: the only node:* touch is the guarded dynamic
 * `import("node:fs/promises" as string)` idiom (keeps bundlers from statically
 * resolving node built-ins). Domain-agnostic: the byte loader, a generic enable
 * skeleton, and a backend singleton.
 *
 * Each consumer owns: its artifact URL (computed in ITS OWN module so
 * `import.meta.url` resolves to that package's `dist`), its wasm-bindgen glue
 * import, and its backend interface. Those CANNOT live here.
 */

export interface LoadOptions {
  readonly wasmBytes?: Uint8Array;
  readonly wasmUrl?: string | URL;
}

/**
 * Resolve raw wasm bytes for the current environment. `fallbackUrl` is the
 * consumer's `new URL('./artifacts/<name>_bg.wasm', import.meta.url)` — it MUST
 * be evaluated in the consumer's module, never here.
 *
 * Resolution: explicit bytes -> explicit URL -> fs (Node) -> fetch
 * (browser/Workers/bundler). Any failure throws.
 */
export async function resolveWasmBytes(
  opts: LoadOptions,
  fallbackUrl: URL,
): Promise<Uint8Array> {
  if (opts.wasmBytes !== undefined) {
    if (opts.wasmBytes.byteLength === 0) throw new Error("empty wasmBytes");
    return opts.wasmBytes;
  }
  const url = opts.wasmUrl ?? fallbackUrl;
  const isNode =
    typeof process !== "undefined" &&
    process.versions?.node !== undefined &&
    (url instanceof URL
      ? url.protocol === "file:"
      : String(url).startsWith("file:"));

  if (isNode) {
    const fs = await import("node:fs/promises" as string);
    const buf = await fs.readFile(url as URL);
    return new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
  }
  const resp = await fetch(url as URL);
  if (!resp.ok) throw new Error(`fetch wasm failed: ${resp.status}`);
  return new Uint8Array(await resp.arrayBuffer());
}

/** A module-singleton backend registry (mirrors the setSyncEmbedder(null) pattern). */
export interface BackendRegistry<B> {
  get(): B | null;
  set(b: B | null): void;
}

export function createBackendRegistry<B>(): BackendRegistry<B> {
  let backend: B | null = null;
  return {
    get: () => backend,
    set: (b) => {
      backend = b;
    },
  };
}

export interface EnableOptions extends LoadOptions {
  /** Throw instead of falling back to pure-TS when the module can't load. */
  readonly require?: boolean;
}

/**
 * Generic opt-in enable skeleton. Resolves bytes, runs the per-domain
 * `instantiate` (glue import + adapt to the domain backend), then `register`s
 * it. Returns true on success; on failure returns false (pure-TS stays active)
 * unless `require:true`.
 *
 * The `_enabled` idempotency guard stays in each consumer's `index.ts` (so the
 * lazy dynamic import of the consumer's loader/glue is skipped for default
 * users); this helper assumes the caller already decided to (re)load.
 */
export async function enableWasmBackend<B>(
  opts: EnableOptions,
  instantiate: (bytes: Uint8Array) => Promise<B>,
  register: (b: B) => void,
  fallbackUrl: URL,
): Promise<boolean> {
  try {
    const bytes = await resolveWasmBytes(opts, fallbackUrl);
    const backend = await instantiate(bytes);
    register(backend);
    return true;
  } catch (err) {
    if (opts.require) throw err;
    // Debug-level so a broken artifact isn't wholly silent, without noise for
    // the normal "no artifact present" path.
    console.debug("[goldenmatch-wasm-runtime] enable fell back to pure-TS:", err);
    return false;
  }
}
