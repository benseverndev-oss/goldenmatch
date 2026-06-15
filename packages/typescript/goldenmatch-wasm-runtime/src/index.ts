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
  /**
   * A standard base64 encoding of the raw `.wasm` bytes. The UNIVERSAL loader
   * strategy (see `decodeWasmBase64`): a consumer can pass the contents of its
   * generated `<name>_base64` module here and the bytes resolve with NO fetch /
   * fs / `import.meta.url` — the one path that works edge-safe in Workers + Deno
   * + every bundler. Takes precedence over `wasmUrl` (but not explicit
   * `wasmBytes`). The decode is pure-JS (`atob` where present, else a Node
   * Buffer fallback), so it stays edge-safe.
   */
  readonly wasmBase64?: string;
}

/**
 * Decode a standard-base64 string to raw bytes, edge-safe (no `node:*`). Uses
 * the global `atob` (browser / Workers / Deno / Node 16+) when present, else a
 * `Buffer` fallback for older Node. Exported so a consumer can decode its
 * generated base64 module directly if it wants the bytes without `enableWasm`.
 */
export function decodeWasmBase64(b64: string): Uint8Array {
  const g = globalThis as { atob?: (s: string) => string; Buffer?: typeof Buffer };
  if (typeof g.atob === "function") {
    const bin = g.atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  if (typeof g.Buffer !== "undefined") {
    const buf = g.Buffer.from(b64, "base64");
    return new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
  }
  throw new Error("no base64 decoder available (atob / Buffer both absent)");
}

/**
 * Resolve raw wasm bytes for the current environment. `fallbackUrl` is the
 * consumer's `new URL('./artifacts/<name>_bg.wasm', import.meta.url)` — it MUST
 * be evaluated in the consumer's module, never here.
 *
 * Resolution: explicit bytes -> explicit base64 -> explicit URL -> fs (Node) ->
 * fetch (browser/Workers/bundler). Any failure throws.
 *
 * `wasmBase64` is the UNIVERSAL strategy: a consumer that wires its generated
 * `<name>_base64` module into `opts.wasmBase64` resolves with no fetch/fs/
 * import.meta.url, which is the only path that is reliably edge-safe in
 * Cloudflare Workers + Deno + every bundler.
 */
export async function resolveWasmBytes(
  opts: LoadOptions,
  fallbackUrl: URL,
): Promise<Uint8Array> {
  if (opts.wasmBytes !== undefined) {
    if (opts.wasmBytes.byteLength === 0) throw new Error("empty wasmBytes");
    return opts.wasmBytes;
  }
  if (opts.wasmBase64 !== undefined && opts.wasmBase64.length > 0) {
    const bytes = decodeWasmBase64(opts.wasmBase64);
    if (bytes.byteLength === 0) throw new Error("empty wasmBase64");
    return bytes;
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
