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
