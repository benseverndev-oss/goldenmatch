# Universal WASM loader strategy — the A1 decision (R1 Workstream A)

**Date:** 2026-06-14 • **Scope:** `score-wasm` opt-in scorer backend, behind
`enableWasm()` only. Flips no default; pure-TS stays the default + fallback.

## The question

Today the opt-in WASM loader resolves the `.wasm` artifact via
`new URL('./artifacts/score_wasm_bg.wasm', import.meta.url)` plus a
"copy the artifact to every plausible `dist` parent" hack
(`copy_wasm_artifact.mjs`). That is finicky for bundlers and is not portable to
Cloudflare Workers / Deno, where `import.meta.url`-relative *asset* resolution
(fetch/fs of a sibling file) is unavailable or unreliable. R1 Workstream A must
prove the kernel loads across all four JS targets (Node / browser / Workers /
Deno) with **no per-target hacks** — so the loader needs ONE universal strategy.

## Options considered

- **(i) base64-INLINE the `.wasm`.** Emit a tiny JS module
  (`score_wasm_base64.js`, exporting `WASM_BASE64`) at build time; the loader
  decodes it to bytes and instantiates. No `fetch`, no `node:fs`, no
  `import.meta.url`-relative asset resolution.
  - **Pro:** works in EVERY target + every bundler with zero branching — a
    base64 string is just JS. This is the canonical "wasm in a Worker" recipe
    (Workers forbids dynamic `fetch` of a bundled asset path; an inlined module
    is bundled like any other code).
  - **Con:** bundle size. base64 is ~4/3 the size of the raw bytes.

- **(ii) ship the `.wasm` asset + a runtime-branching loader.** Detect the
  runtime and pick Node `fs` / browser `fetch` / Workers `WebAssembly.Module`
  import / Deno `Deno.readFile` or fetch.
  - **Pro:** no size overhead; the `.wasm` ships as-is.
  - **Con:** this IS the per-target-hack surface the kill-criterion forbids.
    Each target needs its own asset-resolution branch, Workers in particular
    needs a bundler-specific `import ... from './x.wasm'` (esbuild/wrangler
    each differ), and Deno vs browser differ on `import.meta.url` asset access.
    Exactly the finicky matrix this workstream exists to eliminate.

## Decision: Option (i), base64-inline — behind the existing `enableWasm()` opt-in.

The universal path is selected by `enableWasm({ universal: true })`. It imports
the generated `./artifacts/score_wasm_base64.js`, decodes via
`decodeWasmBase64` (in `goldenmatch-wasm-runtime`: `atob` where present — browser
/ Workers / Deno / Node 16+ — else a Node `Buffer` fallback), and feeds the
bytes through the unchanged `enableWasmBackend` instantiation. No new default
path: default users (and `enableWasm()` without `universal`) keep the existing
URL/fs/fetch loader, and any failure (including the base64 module being absent in
a default checkout) cleanly falls back to pure-TS.

This keeps the contract intact: `enableWasm()` is opt-in, returns `false` on any
load failure (pure-TS stays active), `{ require: true }` throws.

## Bundle-size cost (measured, 2026-06-14)

| artifact | bytes |
|---|---|
| `score_wasm_bg.wasm` (raw) | 115,155 |
| base64 string (`WASM_BASE64`) | 153,540 |
| `score_wasm_base64.js` module | 153,725 |

So the universal strategy costs **~+33.3%** over the raw `.wasm` (≈ +37.5 KB on
this artifact), the expected base64 4/3 inflation. For a 112 KB kernel that is a
~+37 KB one-time bundle cost — paid only by consumers who opt into
`{ universal: true }`. Acceptable for the edge-portability win; revisit if the
kernel grows (a brotli-of-base64 or a `WebAssembly.compileStreaming` asset path
could claw back the inflation for the browser-only case, but that reintroduces
per-target branching).

## Build wiring

`score-wasm/build_wasm.sh` now also emits `score_wasm_base64.js` next to the
`.wasm` (gitignored like every other artifact). CI's WASM build step gets it for
free. The base64 module is consumed only via the lazy dynamic import inside the
universal loader, so default users never load it.
