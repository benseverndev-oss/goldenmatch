/**
 * universal-loader.ts — the A1 universal-loader strategy (R1 Workstream A).
 *
 * THE DECISION (see docs/superpowers/notes/2026-06-14-wasm-universal-loader.md):
 * base64-INLINE the `.wasm` (Option i) rather than ship the asset + a
 * runtime-branching fetch/fs loader (Option ii). The inline path needs NO
 * `fetch`, NO `node:fs`, NO `import.meta.url`-relative asset resolution — it is
 * the ONLY strategy that loads edge-safe across all four JS targets
 * (Node / browser / Cloudflare Workers / Deno) AND every bundler with zero
 * per-target hacks. Trade-off: bundle size (base64 is ~4/3 of the raw `.wasm`;
 * the 115 KB artifact becomes a ~154 KB string — measured in the note). The
 * default `enableWasm()` path is UNCHANGED (URL/fs/fetch); this universal path
 * is a SEPARATE opt-in seam (`enableWasm({ universal: true })`), so default
 * users still load zero wasm bytes.
 *
 * The generated `./artifacts/score_wasm_base64.js` module (emitted by
 * build_wasm.sh, gitignored like the `.wasm` itself) exports `WASM_BASE64`. It
 * is absent in a default checkout, so the dynamic import is wrapped — a missing
 * module turns into the standard pure-TS fallback, exactly like the absent
 * `.wasm` artifact does for the default loader.
 */

/**
 * Resolve the inlined base64 of the score-wasm artifact, or null when the
 * generated module is absent (default checkout / no build). Edge-safe: the only
 * I/O is the dynamic import of a sibling JS module (no fs/fetch/import.meta.url).
 */
export async function loadInlinedWasmBase64(): Promise<string | null> {
  try {
    const mod = (await import("./artifacts/score_wasm_base64.js" as string)) as {
      WASM_BASE64?: string;
    };
    const b64 = mod.WASM_BASE64;
    return typeof b64 === "string" && b64.length > 0 ? b64 : null;
  } catch {
    // Module absent (default checkout) — caller falls back to the URL loader,
    // and ultimately to pure-TS.
    return null;
  }
}
