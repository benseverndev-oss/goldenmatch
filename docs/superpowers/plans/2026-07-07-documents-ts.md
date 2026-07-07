# Document Ingest TS surface (WASM kernels + parity) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the five `documents-core` kernels in the TypeScript `goldenmatch` package via `documents-wasm`, proven byte-identical to Rust + Python by a parity test over the SHARED corpus.

**Architecture:** Mirror the established `suggest-wasm` -> TS precedent EXACTLY: a `build_documents_wasm.mjs` regen script (`wasm-pack build --target web`, strip the async init glue, base64-inline the `.wasm`, copy the golden corpus) writes COMMITTED `_wasm` bindings/bytes so `tsc`/`vitest` need no Rust toolchain; a `documentsWasm.ts` sync loader wraps the 5 kernels as camelCase TS fns; a `documents-core.parity.test.ts` replays the shared JSONL corpus and asserts TS == `expected`.

**Tech Stack:** TypeScript, wasm-bindgen (via `documents-wasm`), wasm-pack, vitest, tsup, Node ESM build script.

**Spec:** `docs/superpowers/specs/2026-07-07-documents-ts-design.md`

---

## Conventions for every task

- **Worktree:** `D:/show_case/gm-docs-ts` (branch `feat/documents-ts`). Do NOT push, do NOT touch `main`. **Never `git stash`** — the stash stack is shared across ALL worktrees of this repo; a pop here can apply/destroy an unrelated worktree's WIP. To compare revs use `git diff <rev>` / `git show <rev>:<path>`.
- **Package dir:** `D:/show_case/gm-docs-ts/packages/typescript/goldenmatch` (call it `$TS` below).
- **The box OOMs on full `vitest`/`tsup` builds.** NEVER run the whole suite or a full build locally. Run ONLY the single parity file: `npx vitest run tests/parity/documents-core.parity.test.ts`. Typecheck a single file with `npx tsc --noEmit` only if it does not balloon memory; otherwise rely on CI.
- **Commit trailers:** copy the `Co-Authored-By` / `Claude-Session` trailers from `git log -1 --format=%B`. Commit with `git -c commit.gpgsign=false commit`.
- **Do NOT hand-write or fake the generated `_wasm/documents*` files.** They come from a real `wasm-pack` regen (Task 2) or CI regenerates them. If `wasm-pack` will not run on this box, author everything else correctly and let CI's `documents_wasm` job be the authoritative build+parity gate (see Task 2's fallback).

---

## Background the implementer needs

**The five kernels** (`packages/rust/extensions/documents-wasm/src/lib.rs`, already merged) export string-in/string-out, snake_case JSON:

| wasm export | signature | returns |
|---|---|---|
| `schema_validate(schema_json)` | one JSON string | canonical schema JSON string (round-trip) |
| `parse_message_text(resp_json)` | the OpenAI response as a JSON string | the message content as a RAW string (not JSON-quoted); throws on truncation/malformed |
| `extract_instruction(schema_json)` | schema JSON string | the extract-prompt as a RAW string |
| `suggest_prompt()` | (none) | the suggest-prompt as a RAW string |
| `normalize_record(values_json, confidence_json, schema_json)` | three JSON strings | `{"values": {...}, "confidence": {...}}` JSON string |

A kernel `JsError` surfaces in JS as a thrown `Error`.

**The shared corpus** is `packages/python/goldenmatch/tests/parity/documents_corpus.jsonl` (30 rows, Python-authored by `scripts/gen_documents_corpus.py`, already asserted `== native` in the Python parity lane). Each row is `{kernel, input, expected}`. Kernel-label distribution: `schema` (10), `parse` (10), `normalize` (6), `prompt_extract` (3), `prompt_suggest` (1). 11 of the 30 rows are error rows (`expected.error == true`); one of those carries `expected.substring == "truncated"`.

**Row shapes (read these carefully — the comparison convention differs per kernel):**
- `schema`: `input` is a schema dict; `expected.ok` is the canonical schema object (e.g. `{"fields":[{"hint":null,"kind":"text","name":"full_name"}]}`). Compare `JSON.parse(schema_validate(JSON.stringify(input)))` `toEqual` `expected.ok`.
- `parse`: `input` is a response dict (e.g. `{"choices":[{"message":{"content":"hello"}}]}`); `expected.ok` is the content STRING (`"hello"`). Compare the RAW kernel string `===`/`toEqual` `expected.ok` (do NOT `JSON.parse` it).
- `prompt_extract`: `input` is a schema dict; `expected.ok` is the prompt STRING. Compare raw string.
- `prompt_suggest`: `input` is `null`/ignored; `expected.ok` is the suggest-prompt STRING. Compare raw string.
- **`normalize` (the #1 correctness trap):** `input` is `{"values":{...},"confidence":{...},"schema":{...}}`. `expected.ok` is NOT the raw `{values,confidence}` object — it is **ORDERED `[col, val]` pair arrays keyed to the schema's field order**: `{"values": [["a","Ada"],["n","90210"],...], "confidence": [["a",0.9],["n",0.0],...]}`. The kernel returns an OBJECT (`{values:{...},confidence:{...}}`); the test MUST reshape it into ordered pairs before comparing, EXACTLY as the Python oracle `_run_native` does (`cols = [f["name"] for f in input.schema.fields]`, then `[[c, values[c]] for c in cols]` and `[[c, Number(confidence[c] ?? 0)] for c in cols]`). A direct `JSON.parse(...) === expected` fails every normalize row.

**Error rows:** assert the TS kernel THROWS (error-by-outcome, not exact message). If the row has `expected.substring`, additionally assert the thrown message includes it (the `"truncated"` truncation message is shared verbatim across Rust/Python/wasm).

**The Python oracle to mirror** is `packages/python/goldenmatch/tests/parity/test_documents_parity.py` — its `_run_native` and `_KERNEL_SYMBOL` are the canonical dispatch + reshape. Read it before writing the TS test.

---

## File structure (locked)

| path (under `$TS` unless noted) | change | responsibility |
|---|---|---|
| `scripts/build_documents_wasm.mjs` | Create | regen: build the wasm, strip async init, write the 3 `_wasm/documents*` files, copy the corpus |
| `src/core/_wasm/documentsWasmBindings.js` + `.d.ts` | Create (generated) | wasm-bindgen glue, async path stripped |
| `src/core/_wasm/documentsWasmBytes.ts` | Create (generated) | base64 of the `.wasm` |
| `src/core/documentsWasm.ts` | Create | sync loader + 5 camelCase wrappers + `documentsWasmAvailable()` |
| `tests/parity/fixtures/documents/documents_corpus.jsonl` | Create (copied) | the shared corpus, copied from the Python oracle by the build script |
| `tests/parity/documents-core.parity.test.ts` | Create | TS-WASM == corpus `expected` (the parity gate) |
| `package.json` | Modify | add subpath export `./core/documents-wasm` |
| `tsup.config.ts` | Modify | add build entry `"core/documentsWasm": "src/core/documentsWasm.ts"` |
| `.github/workflows/ci.yml` (repo root) | Modify | add `documents_wasm` path-filter + a rebuild/verify step |

---

## Task 1: The regen build script

**Files:** Create `$TS/scripts/build_documents_wasm.mjs`

Mirror `$TS/scripts/build_suggest_wasm.mjs` (read it first) with these deltas: crate = `documents-wasm`; wasm-pack output basename = `goldenmatch_documents_wasm`; the `.d.ts` declares all FIVE exports; the corpus is a SINGLE Python-authored JSONL copied from `packages/python/goldenmatch/tests/parity/`, not a directory of Rust-blessed golden JSONs.

- [ ] **Step 1: Write the script** exactly:

```js
#!/usr/bin/env node
/**
 * build_documents_wasm.mjs — build the documents-core wasm kernels and embed
 * them into the TS package so the JS/TS surface consumes the SAME document-
 * ingest kernels as the Python -native wheel (one source of truth).
 *
 * Produces (all committed, so tsc/vitest/tsup need NO rust toolchain — only
 * this regen step does; requires wasm-pack + the wasm32-unknown-unknown target):
 *   - src/core/_wasm/documentsWasmBindings.js / .d.ts   wasm-bindgen glue (async path stripped)
 *   - src/core/_wasm/documentsWasmBytes.ts              base64 of the .wasm (edge-safe)
 *   - tests/parity/fixtures/documents/documents_corpus.jsonl  the shared corpus, copied
 *
 * The corpus is Python-authored (scripts/gen_documents_corpus.py) and already
 * asserted ==native in the Python parity lane; this script COPIES it so the TS
 * parity test runs the identical vectors. Async init is stripped for the same
 * reason as suggest-wasm (keeps src/core edge-safe: no node:url / fetch).
 *
 * Usage: node scripts/build_documents_wasm.mjs
 */
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync, mkdirSync, copyFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const tsPkg = resolve(here, "..");
const wasmCrate = resolve(tsPkg, "../../rust/extensions/documents-wasm");
const pkgDir = resolve(wasmCrate, "pkg");
const outWasmDir = resolve(tsPkg, "src/core/_wasm");
const fixturesDir = resolve(tsPkg, "tests/parity/fixtures/documents");
const corpusSrc = resolve(
  tsPkg,
  "../../python/goldenmatch/tests/parity/documents_corpus.jsonl",
);

function run(cmd, args, cwd) {
  console.log(`$ ${cmd} ${args.join(" ")}`);
  execFileSync(cmd, args, { cwd, stdio: "inherit" });
}

// 1. Build the wasm (wasm-opt disabled in the crate's Cargo.toml so the build
//    is hermetic — no binaryen download in a network-restricted CI).
run("wasm-pack", ["build", wasmCrate, "--target", "web"], tsPkg);

mkdirSync(outWasmDir, { recursive: true });
mkdirSync(fixturesDir, { recursive: true });

// 2. Glue JS: neutralize the async init path, re-export only initSync.
function assertReplace(src, needle, repl, label) {
  if (!src.includes(needle)) {
    throw new Error(
      `build_documents_wasm: expected to find ${label} in the wasm-bindgen ` +
        `glue but did not. The wasm-bindgen output format likely changed — ` +
        `re-inspect the glue and update this script.`,
    );
  }
  return src.replace(needle, repl);
}
let glue = readFileSync(resolve(pkgDir, "goldenmatch_documents_wasm.js"), "utf8");
glue = assertReplace(
  glue,
  "module_or_path = new URL('goldenmatch_documents_wasm_bg.wasm', import.meta.url);",
  "module_or_path = undefined; // import.meta.url stripped (async path unused; keeps src/core edge-safe)",
  "the import.meta.url URL construction",
);
glue = assertReplace(
  glue,
  "export { initSync, __wbg_init as default };",
  "export { initSync };",
  "the module export line",
);
const banner =
  "/* AUTO-GENERATED by scripts/build_documents_wasm.mjs — DO NOT EDIT.\n" +
  "   wasm-bindgen glue for goldenmatch-documents-wasm, async init path stripped. */\n";
writeFileSync(resolve(outWasmDir, "documentsWasmBindings.js"), banner + glue);

// 3. Hand-fixed minimal .d.ts (stable across wasm-bindgen versions) — all 5 exports.
writeFileSync(
  resolve(outWasmDir, "documentsWasmBindings.d.ts"),
  `/* AUTO-GENERATED by scripts/build_documents_wasm.mjs — DO NOT EDIT. */
export function schema_validate(schema_json: string): string;
export function parse_message_text(resp_json: string): string;
export function extract_instruction(schema_json: string): string;
export function suggest_prompt(): string;
export function normalize_record(
  values_json: string,
  confidence_json: string,
  schema_json: string,
): string;
export type SyncInitInput = BufferSource | WebAssembly.Module;
export function initSync(
  module: { module: SyncInitInput } | SyncInitInput,
): unknown;
`,
);

// 4. base64-embed the wasm bytes (edge-safe: decoded via atob, no node:Buffer).
const wasmBytes = readFileSync(
  resolve(pkgDir, "goldenmatch_documents_wasm_bg.wasm"),
);
const b64 = wasmBytes.toString("base64");
writeFileSync(
  resolve(outWasmDir, "documentsWasmBytes.ts"),
  `/* AUTO-GENERATED by scripts/build_documents_wasm.mjs — DO NOT EDIT.
 * base64 of goldenmatch_documents_wasm_bg.wasm (${wasmBytes.length} bytes).
 * Inlined so the loader stays synchronous + edge-safe (no fs / fetch). */
export const DOCUMENTS_WASM_BASE64 =
  "${b64}";
`,
);

// 5. Copy the shared Python-authored corpus into the TS parity fixtures.
copyFileSync(corpusSrc, resolve(fixturesDir, "documents_corpus.jsonl"));

console.log(
  `\nDone. wasm ${wasmBytes.length} B -> ${b64.length} B base64; ` +
    `bindings + bytes + corpus written.`,
);
```

- [ ] **Step 2: Commit** (script only; generated outputs land in Task 2):

```bash
cd D:/show_case/gm-docs-ts
git add packages/typescript/goldenmatch/scripts/build_documents_wasm.mjs
git -c commit.gpgsign=false commit -m "build(ts): add documents-wasm regen script"
```

---

## Task 2: Regenerate the committed bindings, bytes, and fixtures

**Files:** Create (generated) `$TS/src/core/_wasm/documentsWasmBindings.js`, `documentsWasmBindings.d.ts`, `documentsWasmBytes.ts`, and `$TS/tests/parity/fixtures/documents/documents_corpus.jsonl`

- [ ] **Step 1: Probe for the toolchain.**

```bash
command -v wasm-pack && rustup target list --installed | grep wasm32-unknown-unknown
```

- [ ] **Step 2a (toolchain present): run the regen.**

```bash
cd D:/show_case/gm-docs-ts/packages/typescript/goldenmatch
node scripts/build_documents_wasm.mjs
```

Expected: `Done. wasm <N> B -> <M> B base64; bindings + bytes + corpus written.` and the four files above now exist. If `assertReplace` throws (glue format changed), STOP and report — do not hand-edit the glue.

- [ ] **Step 2b (toolchain ABSENT / exFAT build fails): honest fallback.** Do NOT fabricate the `_wasm` files. Skip to Task 3, author `documentsWasm.ts`, the parity test (Task 4), the exports/tsup wiring (Task 5), and the CI job (Task 6). The parity test cannot run locally without the bindings — that is expected; **CI's `documents_wasm` job is the authoritative regen + parity gate**. Note in the Task 4 commit body that local parity was deferred to CI. Report status `DONE_WITH_CONCERNS` naming the exact blocker (e.g. `wasm-pack not on PATH` / the exFAT `cargo.exe` PATH mangling from prior memory).

- [ ] **Step 3: Commit the generated outputs** (only if Step 2a ran):

```bash
cd D:/show_case/gm-docs-ts
git add packages/typescript/goldenmatch/src/core/_wasm/documentsWasmBindings.js \
        packages/typescript/goldenmatch/src/core/_wasm/documentsWasmBindings.d.ts \
        packages/typescript/goldenmatch/src/core/_wasm/documentsWasmBytes.ts \
        packages/typescript/goldenmatch/tests/parity/fixtures/documents/documents_corpus.jsonl
git -c commit.gpgsign=false commit -m "build(ts): regenerate embedded documents wasm + corpus fixture"
```

---

## Task 3: The `documentsWasm.ts` loader + wrappers

**Files:** Create `$TS/src/core/documentsWasm.ts`

Mirror `$TS/src/core/suggestWasm.ts`'s init pattern (lazy `initSync` over base64, `atob` decode — no `node:*`), but there is NO backend registry to wire (documents-wasm is pure kernels), so this file is just the loader + 5 wrappers + an availability probe.

- [ ] **Step 1: Write the file** exactly:

```ts
/**
 * documentsWasm.ts — synchronous, edge-safe loader for the document-ingest
 * kernels (the `goldenmatch-documents-core` Rust crate, compiled to wasm via
 * `documents-wasm`).
 *
 * These are the SAME kernels the Python `goldenmatch-native` wheel calls, so
 * schema validation, response parsing, prompt building, and record normalization
 * are byte-identical across Python / Rust / TS — proven by the shared corpus
 * (`tests/parity/fixtures/documents/documents_corpus.jsonl`).
 *
 * Edge-safe: no `node:*` imports. The wasm is inlined as base64 and instantiated
 * synchronously via wasm-bindgen's `initSync`, so the API stays sync and works in
 * browsers / Workers / edge runtimes.
 *
 * Scope: the deterministic KERNELS only. Rasterization and the VLM call (the I/O
 * half of ingest) are not part of this surface.
 */
import {
  initSync,
  schema_validate,
  parse_message_text,
  extract_instruction,
  suggest_prompt,
  normalize_record,
} from "./_wasm/documentsWasmBindings.js";
import { DOCUMENTS_WASM_BASE64 } from "./_wasm/documentsWasmBytes.js";

// ---------------------------------------------------------------------------
// Types (mirror the Python/Rust contract)
// ---------------------------------------------------------------------------

export type FieldKind =
  | "text"
  | "email"
  | "phone"
  | "address"
  | "date"
  | "number";

export interface SchemaField {
  name: string;
  kind?: FieldKind;
  hint?: string | null;
}

export interface TargetSchema {
  fields: SchemaField[];
}

export interface NormalizedRecord {
  values: Record<string, unknown>;
  confidence: Record<string, number>;
}

// ---------------------------------------------------------------------------
// wasm init (lazy, once)
// ---------------------------------------------------------------------------

let initialized = false;

function base64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64); // available in browsers, Workers, Node >= 18
  const len = bin.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function ensureInit(): void {
  if (initialized) return;
  initSync({ module: base64ToBytes(DOCUMENTS_WASM_BASE64) });
  initialized = true;
}

// ---------------------------------------------------------------------------
// Kernels (camelCase wrappers; JSON at the boundary, snake_case JSON kernels)
// ---------------------------------------------------------------------------

/** Validate + canonicalize a target schema (round-trip). Throws on a bad schema. */
export function validateSchema(schema: TargetSchema): TargetSchema {
  ensureInit();
  return JSON.parse(schema_validate(JSON.stringify(schema))) as TargetSchema;
}

/** Extract the message content from an OpenAI-style response. Throws on
 *  truncation (finish_reason=length) or a malformed envelope. Returns the RAW
 *  content string (not JSON). */
export function parseMessageText(resp: unknown): string {
  ensureInit();
  return parse_message_text(JSON.stringify(resp));
}

/** Build the per-schema extraction instruction. Throws on a bad schema. */
export function extractInstruction(schema: TargetSchema): string {
  ensureInit();
  return extract_instruction(JSON.stringify(schema));
}

/** The (schema-independent) suggest prompt. */
export function suggestPrompt(): string {
  ensureInit();
  return suggest_prompt();
}

/** Normalize a partial extraction into schema columns (missing -> null, coerced
 *  types, etc.). Returns `{values, confidence}` keyed by column name. */
export function normalizeRecord(
  values: Record<string, unknown>,
  confidence: Record<string, number>,
  schema: TargetSchema,
): NormalizedRecord {
  ensureInit();
  return JSON.parse(
    normalize_record(
      JSON.stringify(values),
      JSON.stringify(confidence),
      JSON.stringify(schema),
    ),
  ) as NormalizedRecord;
}

/** True if the wasm kernels initialize + run in this environment. Lets the
 *  parity harness statically skip where wasm can't load (mirrors suggest-wasm's
 *  enable/try-catch). */
export function documentsWasmAvailable(): boolean {
  try {
    ensureInit();
    suggest_prompt(); // benign smoke call
    return true;
  } catch {
    return false;
  }
}
```

- [ ] **Step 2: Commit:**

```bash
cd D:/show_case/gm-docs-ts
git add packages/typescript/goldenmatch/src/core/documentsWasm.ts
git -c commit.gpgsign=false commit -m "feat(ts): documents-core wasm kernel wrappers"
```

---

## Task 4: The parity test

**Files:** Create `$TS/tests/parity/documents-core.parity.test.ts`

Read `$TS/tests/parity/fingerprint-wasm.parity.test.ts` for the `readFileSync` corpus pattern and `$TS/tests/parity/suggest-wasm.parity.test.ts` for the static-skip pattern. The corpus is JSONL (one row per line), not a per-case JSON directory — parse it line by line. Mirror the Python `_run_native` dispatch + the normalize reshape.

- [ ] **Step 1: Write the failing test** exactly:

```ts
/**
 * Cross-surface parity: the TS documents-core wasm kernels vs the SHARED corpus
 * (`documents_corpus.jsonl`, Python-authored, asserted ==native in the Python
 * parity lane, copied into the TS fixtures by `scripts/build_documents_wasm.mjs`).
 * Green here == one kernel set, three surfaces (Python / Rust / TS), zero drift.
 *
 * Per-kernel comparison mirrors the Python oracle `_run_native` in
 * `test_documents_parity.py`:
 *  - schema:                JSON.parse(kernel) deep-equals expected.ok (object)
 *  - parse / prompt_*:      the RAW kernel string equals expected.ok (string)
 *  - normalize:             reshape the kernel's {values,confidence} object into
 *                           ORDERED [col,val] pairs (schema field order) BEFORE
 *                           comparing — expected.ok is ordered pairs, NOT the raw
 *                           object. (The #1 trap; see the design doc.)
 *  - error rows:            the kernel THROWS; if expected.substring is set, the
 *                           thrown message includes it.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import {
  validateSchema,
  parseMessageText,
  extractInstruction,
  suggestPrompt,
  normalizeRecord,
  documentsWasmAvailable,
  type TargetSchema,
} from "../../src/core/documentsWasm.js";

interface Row {
  kernel: "schema" | "parse" | "prompt_extract" | "prompt_suggest" | "normalize";
  input: any;
  expected: { ok?: unknown; error?: boolean; substring?: string };
}

const here = dirname(fileURLToPath(import.meta.url));
const rows: Row[] = readFileSync(
  resolve(here, "fixtures/documents/documents_corpus.jsonl"),
  "utf8",
)
  .split("\n")
  .map((l) => l.trim())
  .filter((l) => l.length > 0)
  .map((l) => JSON.parse(l) as Row);

// Reshape the normalize kernel's object output into ordered [col,val] pairs,
// keyed to the schema's field order — exactly as the Python oracle does.
function normalizeToPairs(input: any): {
  values: [string, unknown][];
  confidence: [string, number][];
} {
  const cols: string[] = input.schema.fields.map((f: any) => f.name);
  const out = normalizeRecord(input.values, input.confidence, input.schema as TargetSchema);
  return {
    values: cols.map((c) => [c, out.values[c] ?? null]),
    confidence: cols.map((c) => [c, Number(out.confidence[c] ?? 0)]),
  };
}

// Run one kernel for the SUCCESS path; returns a value shaped like expected.ok.
function runOk(row: Row): unknown {
  switch (row.kernel) {
    case "schema":
      return validateSchema(row.input as TargetSchema);
    case "parse":
      return parseMessageText(row.input);
    case "prompt_extract":
      return extractInstruction(row.input as TargetSchema);
    case "prompt_suggest":
      return suggestPrompt();
    case "normalize":
      return normalizeToPairs(row.input);
  }
}

const available = documentsWasmAvailable();
const maybe = available ? it : it.skip;

describe("documents-core wasm parity (TS == shared corpus)", () => {
  it("loaded a non-trivial corpus", () => {
    expect(rows.length).toBeGreaterThanOrEqual(20);
  });

  rows.forEach((row, i) => {
    maybe(`${row.kernel}[${i}]: ${row.expected.error ? "throws" : "matches expected.ok"}`, () => {
      if (row.expected.error) {
        expect(() => runOk(row)).toThrow();
        if (row.expected.substring) {
          expect(() => runOk(row)).toThrow(new RegExp(row.expected.substring));
        }
        return;
      }
      expect(runOk(row)).toEqual(row.expected.ok);
    });
  });
});
```

- [ ] **Step 2: Run it.**

Run: `cd D:/show_case/gm-docs-ts/packages/typescript/goldenmatch && npx vitest run tests/parity/documents-core.parity.test.ts`

- If Task 2a regenerated the bindings: Expected PASS (the "loaded a non-trivial corpus" test always runs; the 30 kernel cases run and pass).
- If Task 2b (no toolchain): the import of `documentsWasmBytes.js`/bindings won't resolve, so the run fails at module resolution. That is the expected local state — the assertion logic is still correct and CI will exercise it. Do NOT fake the bindings to force a local green.

- [ ] **Step 3: Commit:**

```bash
cd D:/show_case/gm-docs-ts
git add packages/typescript/goldenmatch/tests/parity/documents-core.parity.test.ts
git -c commit.gpgsign=false commit -m "test(ts): documents-core wasm parity over the shared corpus"
```

---

## Task 5: Package export + tsup entry

**Files:** Modify `$TS/package.json`, `$TS/tsup.config.ts`

- [ ] **Step 1: Add the subpath export** to `$TS/package.json`. Find the `"./core/suggest-wasm": {...}` line in `exports` and add DIRECTLY after it (match the forward-slash style of the `autoconfig-wasm`/`suggest-wasm` entries, NOT the backslash-mangled ones):

```json
    "./core/documents-wasm": { "types": "./dist/core/documentsWasm.d.ts", "import": "./dist/core/documentsWasm.js", "require": "./dist/core/documentsWasm.cjs" },
```

- [ ] **Step 2: Add the tsup entry** to `$TS/tsup.config.ts`. Find `"core/suggestWasm": "src/core/suggestWasm.ts",` in the `entry` map and add after it:

```ts
    // Opt-in entry: the documents-core kernels (schema/parse/prompt/normalize)
    // compiled to wasm, so JS/TS shares ONE document-ingest kernel with Python.
    // Carries the inlined wasm base64; a separate subpath
    // (`goldenmatch/core/documents-wasm`), out of the default core graph.
    "core/documentsWasm": "src/core/documentsWasm.ts",
```

- [ ] **Step 3: Validate the JSON** (do NOT run a full build — it OOMs):

```bash
cd D:/show_case/gm-docs-ts/packages/typescript/goldenmatch && node -e "JSON.parse(require('fs').readFileSync('package.json','utf8')); console.log('package.json parses')"
```

Expected: `package.json parses`.

- [ ] **Step 4: Commit:**

```bash
cd D:/show_case/gm-docs-ts
git add packages/typescript/goldenmatch/package.json packages/typescript/goldenmatch/tsup.config.ts
git -c commit.gpgsign=false commit -m "build(ts): export goldenmatch/core/documents-wasm subpath"
```

---

## Task 6: CI path-filter + rebuild/verify job

**Files:** Modify `.github/workflows/ci.yml` (repo root)

Mirror the `suggest_wasm` filter + step. The one DELTA from suggest: the corpus is Python-authored (copied), so the drift-guard error message points at `gen_documents_corpus.py`, not a Rust bless.

- [ ] **Step 1: Add the path filter.** In the `changes` job's `filter` block, directly after the `suggest_wasm:` entry (ends at the `src/core/_wasm/suggestWasm*` line), add:

```yaml
            documents_wasm:
              # Gates the (rust-toolchain-heavy) documents wasm rebuild + drift
              # guard inside the typescript lane. Fires on the shared documents
              # core, the wasm wrapper, the embed script, the TS surface, or the
              # Python-authored corpus the build script copies.
              - 'packages/rust/extensions/documents-core/**'
              - 'packages/rust/extensions/documents-wasm/**'
              - 'packages/typescript/goldenmatch/scripts/build_documents_wasm.mjs'
              - 'packages/typescript/goldenmatch/src/core/documentsWasm.ts'
              - 'packages/typescript/goldenmatch/src/core/_wasm/documentsWasm*'
              - 'packages/python/goldenmatch/tests/parity/documents_corpus.jsonl'
```

- [ ] **Step 2: Wire the job output.** In the `changes` job's `outputs:` map, next to `suggest_wasm: ${{ steps.filter.outputs.suggest_wasm }}`, add:

```yaml
      documents_wasm: ${{ steps.filter.outputs.documents_wasm }}
```

- [ ] **Step 3: Add the rebuild/verify step.** Directly after the `- name: Rebuild + verify embedded suggest wasm` step (in the typescript job), add:

```yaml
      # Same drift-guard pattern for the documents-core kernels. Rebuild the
      # inlined wasm + bindings, copy the Python-authored corpus, diff ONLY the
      # corpus JSONL (deterministic; NOT the toolchain-variant wasm bytes), then
      # prove the committed wasm reproduces it.
      - name: Rebuild + verify embedded documents wasm
        if: needs.changes.outputs.documents_wasm == 'true' || needs.changes.outputs.force_all == 'true'
        working-directory: packages/typescript/goldenmatch
        run: |
          rustup target add wasm32-unknown-unknown
          command -v wasm-pack >/dev/null 2>&1 || curl -sSf https://rustwasm.github.io/wasm-pack/installer/init.sh | sh
          node scripts/build_documents_wasm.mjs
          if ! git diff --exit-code -- tests/parity/fixtures/documents/documents_corpus.jsonl; then
            echo "::error::documents corpus fixture is stale. Regenerate it \
              ('python packages/python/goldenmatch/scripts/gen_documents_corpus.py'), \
              run 'node scripts/build_documents_wasm.mjs' in \
              packages/typescript/goldenmatch, and commit the result." >&2
            exit 1
          fi
          npx vitest run tests/parity/documents-core.parity.test.ts
```

- [ ] **Step 4: Validate the YAML** (a startup-failure here silently drops every job — see the CI-YAML lesson):

```bash
cd D:/show_case/gm-docs-ts && python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci.yml is valid YAML')"
```

Expected: `ci.yml is valid YAML`.

- [ ] **Step 5: Commit:**

```bash
cd D:/show_case/gm-docs-ts
git add .github/workflows/ci.yml
git -c commit.gpgsign=false commit -m "ci: rebuild + parity-verify the documents wasm kernels"
```

---

## Task 7: Final verification

- [ ] **Step 1:** If Task 2a ran (bindings present), re-run the parity test once more end-to-end:
  `cd D:/show_case/gm-docs-ts/packages/typescript/goldenmatch && npx vitest run tests/parity/documents-core.parity.test.ts` — all cases green.
- [ ] **Step 2:** `cd D:/show_case/gm-docs-ts && python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('ok')"` — valid.
- [ ] **Step 3:** `git -C D:/show_case/gm-docs-ts status` — only the intended files touched; no stray edits; no `_wasm/documents*` files unless a real regen produced them.
- [ ] **Step 4:** Confirm NO api_parity change was made — this surface adds no MCP tool / CLI command / A2A skill, so `parity/goldenmatch.yaml` and the skill/tool count tests must be UNTOUCHED. (If you edited them, revert.)

---

## Done-when

- `documentsWasm.ts` exposes `validateSchema` / `parseMessageText` / `extractInstruction` / `suggestPrompt` / `normalizeRecord` (+ `documentsWasmAvailable`) over the wasm kernels.
- `documents-core.parity.test.ts` replays the shared corpus and asserts TS == `expected` for all 30 rows (success + error + the normalize ordered-pairs reshape), green in CI.
- `build_documents_wasm.mjs` regenerates the committed bindings/bytes + copies the corpus; the CI `documents_wasm` job runs it, diff-guards the corpus, and parity-tests.
- `goldenmatch/core/documents-wasm` is an exported subpath with a tsup entry.
- No api_parity manifest / count change (library kernel module, no new agent-facing surface).
- Deferred (explicitly out): rasterization, the VLM call, end-to-end TS `ingestDocuments`/`suggestSchema`.
