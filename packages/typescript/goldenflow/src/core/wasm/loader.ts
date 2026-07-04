/**
 * loader.ts — universal WASM byte loader + instantiation for the goldenflow
 * identifier kernel. Edge-safe: the only node:* touch is the guarded dynamic
 * `import("node:fs/promises" as string)` idiom inside the shared runtime's
 * `resolveWasmBytes`, not here.
 *
 * Resolution order (delegated to `goldenmatch-wasm-runtime`): explicit bytes
 * -> explicit base64 -> explicit URL -> fs (Node) -> fetch (browser/Workers/
 * bundler). Any failure throws; index.ts turns that into the pure-TS
 * fallback (or rethrows under { require: true }).
 */
import {
  resolveWasmBytes as sharedResolveWasmBytes,
  type LoadOptions,
} from "goldenmatch-wasm-runtime";
import type { FlowWasmBackend } from "./backend.js";

export type { LoadOptions };

/**
 * Resolve the raw wasm bytes, pinning goldenflow's own artifact URL (computed
 * here so `import.meta.url` resolves to THIS package's own dist — passing the
 * URL through the shared runtime would resolve to the wrong location).
 */
export function resolveWasmBytes(opts: LoadOptions): Promise<Uint8Array> {
  return sharedResolveWasmBytes(
    opts,
    new URL("./artifacts/goldenflow_wasm_bg.wasm", import.meta.url),
  );
}

/**
 * Instantiate the goldenflow-wasm module and adapt it to a FlowWasmBackend.
 * Uses the wasm-bindgen `--target web` glue: the default export is the async
 * `init`, which accepts `{ module_or_path: <bytes|url|module> }`.
 */
export async function instantiateBackend(bytes: Uint8Array): Promise<FlowWasmBackend> {
  // Dynamic import of the generated glue (absent in a default checkout).
  const glue = (await import("./artifacts/goldenflow_wasm.js" as string)) as {
    // module_or_path accepts more (URL/Response/Module), but we only ever pass
    // the resolved bytes; typing it as Uint8Array avoids the DOM
    // `BufferSource` lib type (this package typechecks without the DOM lib).
    default: (input: { module_or_path: Uint8Array }) => Promise<unknown>;
    cc_validate: (s: string) => boolean;
    cc_format: (s: string) => string | undefined;
    cc_mask: (s: string) => string | undefined;
    iban_validate: (s: string) => boolean;
    iban_format: (s: string) => string | undefined;
    isbn_validate: (s: string) => boolean;
    isbn_normalize: (s: string) => string | undefined;
    ean_validate: (s: string) => boolean;
    vat_validate: (s: string) => boolean;
    vat_format: (s: string) => string | undefined;
    swift_validate: (s: string) => boolean;
    swift_format: (s: string) => string | undefined;
    aba_validate: (s: string) => boolean;
    imei_validate: (s: string) => boolean;
    name_transliterate: (s: string) => string;
    name_script: (s: string) => string;
    email_lowercase: (s: string) => string;
    email_normalize: (s: string) => string;
    email_extract_domain: (s: string) => string | undefined;
    email_validate: (s: string) => boolean | undefined;
  };
  await glue.default({ module_or_path: bytes });

  return {
    ccValidate: (s) => glue.cc_validate(s),
    ccFormat: (s) => glue.cc_format(s),
    ccMask: (s) => glue.cc_mask(s),
    ibanValidate: (s) => glue.iban_validate(s),
    ibanFormat: (s) => glue.iban_format(s),
    isbnValidate: (s) => glue.isbn_validate(s),
    isbnNormalize: (s) => glue.isbn_normalize(s),
    eanValidate: (s) => glue.ean_validate(s),
    vatValidate: (s) => glue.vat_validate(s),
    vatFormat: (s) => glue.vat_format(s),
    swiftValidate: (s) => glue.swift_validate(s),
    swiftFormat: (s) => glue.swift_format(s),
    abaValidate: (s) => glue.aba_validate(s),
    imeiValidate: (s) => glue.imei_validate(s),
    nameTransliterate: (s) => glue.name_transliterate(s),
    nameScript: (s) => glue.name_script(s),
    emailLowercase: (s) => glue.email_lowercase(s),
    emailNormalize: (s) => glue.email_normalize(s),
    emailExtractDomain: (s) => glue.email_extract_domain(s),
    emailValidate: (s) => glue.email_validate(s) ?? false,
  };
}
