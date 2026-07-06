/**
 * Email transforms — ported from goldenflow/transforms/email.py
 * Side-effect module: registers 4 email transforms on import.
 *
 * Owned-kernel family (D1 wave): each transform is a byte-for-byte port of
 * the Python pure-TS reference (`_email_lowercase_py` et al. in
 * `goldenflow/transforms/email.py`), which is itself proven byte-identical
 * to the Rust `goldenflow-core::email` kernels (parity corpus in
 * `tests/parity/identifiers_corpus.jsonl`). Each transform dispatches to the
 * opt-in WASM backend (`FlowWasmBackend`, a thin wasm-bindgen shim over the
 * SAME Rust kernel) when `enableWasm()` has succeeded; otherwise it runs the
 * pure-TS implementation below. Pure-TS is the default.
 */

import type { ColumnValue } from "../types.js";
import { registerTransform } from "./registry.js";
import { getFlowWasmBackend, type FlowWasmBackend } from "../wasm/backend.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Map a string column through a string-returning fn; nulls and non-strings
 * pass through unchanged (mirrors polars `map_elements` on an Optional[str]
 * input -- `None` in, `None` out). */
function mapStrings(
  values: readonly ColumnValue[],
  fn: (s: string) => string,
): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    return fn(v);
  });
}

/** Map a string column through a boolean-returning fn; nulls and non-strings
 * pass through unchanged. */
function mapToBool(
  values: readonly ColumnValue[],
  fn: (s: string) => boolean,
): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    return fn(v);
  });
}

/** Map a string column through a fn returning `string | undefined` (the Rust
 * `Option<String>` -- `undefined` mirrors `None`); nulls and non-strings pass
 * through unchanged, `undefined` maps to `null` in the output column. */
function mapToStringOrNull(
  values: readonly ColumnValue[],
  fn: (s: string) => string | undefined,
): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    const r = fn(v);
    return r === undefined ? null : r;
  });
}

// ---------------------------------------------------------------------------
// email_lowercase (series, email|string, 55)
//
// Pure-TS reference for goldenflow-core's `email::email_lowercase` kernel.
// Trim + lowercase the whole address. No invalid-input case.
// ---------------------------------------------------------------------------

function emailLowercaseTs(val: string): string {
  return val.trim().toLowerCase();
}

function emailLowercase(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapStrings(values, backend ? (s) => backend.emailLowercase(s) : emailLowercaseTs);
}

registerTransform(
  {
    name: "email_lowercase",
    inputTypes: ["email", "string"],
    autoApply: false,
    priority: 55,
    mode: "series",
  },
  emailLowercase,
);

// ---------------------------------------------------------------------------
// email_normalize (series, email, 50)
//
// Pure-TS reference for goldenflow-core's `email::email_normalize` kernel.
// Lowercase, strip a `+tag` from the local part, and strip dots from the
// local part for Gmail/Googlemail domains. Preserves the ORIGINAL input
// verbatim when the trimmed + lowercased value is empty or has no `@`.
// ---------------------------------------------------------------------------

function emailNormalizeTs(val: string): string {
  const original = val;
  const v = val.trim().toLowerCase();
  if (v === "" || !v.includes("@")) return original;
  const atIdx = v.lastIndexOf("@"); // rsplit("@", 1)
  let local = v.slice(0, atIdx);
  const domain = v.slice(atIdx + 1);
  local = local.split("+")[0] ?? "";
  if (domain === "gmail.com" || domain === "googlemail.com") {
    local = local.replace(/\./g, "");
  }
  return `${local}@${domain}`;
}

function emailNormalize(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapStrings(values, backend ? (s) => backend.emailNormalize(s) : emailNormalizeTs);
}

registerTransform(
  {
    name: "email_normalize",
    inputTypes: ["email"],
    autoApply: false,
    priority: 50,
    mode: "series",
  },
  emailNormalize,
);

// ---------------------------------------------------------------------------
// email_canonical (series, email, 50)
//
// Pure-TS reference for goldenflow-core's `email::email_canonical` kernel:
// email_normalize + alias googlemail.com -> gmail.com so Gmail variants
// collapse completely. Preserves invalid input verbatim (like normalize).
// ---------------------------------------------------------------------------

function emailCanonicalTs(val: string): string {
  const normalized = emailNormalizeTs(val);
  const idx = normalized.lastIndexOf("@");
  if (idx !== -1) {
    const local = normalized.slice(0, idx);
    const domain = normalized.slice(idx + 1);
    if (domain === "googlemail.com") return `${local}@gmail.com`;
  }
  return normalized;
}

function emailCanonical(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapStrings(values, backend ? (s) => backend.emailCanonical(s) : emailCanonicalTs);
}

registerTransform(
  { name: "email_canonical", inputTypes: ["email"], autoApply: false, priority: 50, mode: "series" },
  emailCanonical,
);

// ---------------------------------------------------------------------------
// email_mask (series, email, 30)
//
// Pure-TS reference for goldenflow-core's `email::email_mask` kernel: trim +
// lowercase, keep the first local char, star the rest, keep @domain
// (`John@Example.com` -> `j***@example.com`). `undefined` (-> null) when the
// input has no `@`, an empty local part, or an empty domain.
// ---------------------------------------------------------------------------

function emailMaskTs(val: string): string | undefined {
  const v = val.trim().toLowerCase();
  const idx = v.lastIndexOf("@");
  if (idx === -1) return undefined;
  const local = v.slice(0, idx);
  const domain = v.slice(idx + 1);
  if (local === "" || domain === "") return undefined;
  // Codepoint-count the local part to match Rust `chars().count()` / Python len().
  const localLen = Array.from(local).length;
  return Array.from(local)[0]! + "*".repeat(localLen - 1) + "@" + domain;
}

function emailMask(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapToStringOrNull(values, backend ? (s) => backend.emailMask(s) : emailMaskTs);
}

registerTransform(
  { name: "email_mask", inputTypes: ["email"], autoApply: false, priority: 30, mode: "series" },
  emailMask,
);

// ---------------------------------------------------------------------------
// email_extract_domain (series, email, 40)
//
// Pure-TS reference for goldenflow-core's `email::email_extract_domain`
// kernel. Lowercased domain after the LAST `@`; `undefined` (-> null) if
// there is no `@`, or nothing follows it.
// ---------------------------------------------------------------------------

function emailExtractDomainTs(val: string): string | undefined {
  const v = val.trim();
  const idx = v.lastIndexOf("@");
  if (idx === -1) return undefined;
  const domain = v.slice(idx + 1);
  if (domain === "") return undefined;
  return domain.toLowerCase();
}

function emailExtractDomain(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapToStringOrNull(
    values,
    backend ? (s) => backend.emailExtractDomain(s) : emailExtractDomainTs,
  );
}

registerTransform(
  {
    name: "email_extract_domain",
    inputTypes: ["email"],
    autoApply: false,
    priority: 40,
    mode: "series",
  },
  emailExtractDomain,
);

// ---------------------------------------------------------------------------
// email_validate (series, email|string, 60)
//
// Pure-TS reference for goldenflow-core's `email::email_validate` kernel.
// Hand-rolled equivalent of `^[^@\s]+@[^@\s]+\.[^@\s]+$` (deliberately no
// regex, mirroring the identifier kernels' no-regex parity policy): exactly
// one `@`; a non-empty, whitespace-free local part; a non-empty,
// whitespace-free domain part containing a `.` that is neither the first nor
// the last character. Empty (after trim) input is `false`.
// ---------------------------------------------------------------------------

function hasWhitespace(s: string): boolean {
  for (const c of s) {
    if (/\s/.test(c)) return true;
  }
  return false;
}

function emailValidateTs(val: string): boolean {
  const t = val.trim();
  if (t === "") return false;
  const atCount = t.split("@").length - 1;
  if (atCount !== 1) return false;
  const idx = t.indexOf("@");
  const local = t.slice(0, idx);
  const domain = t.slice(idx + 1);
  if (local === "" || hasWhitespace(local)) return false;
  if (domain === "" || hasWhitespace(domain)) return false;
  for (let i = 0; i < domain.length; i++) {
    if (domain[i] === "." && i !== 0 && i !== domain.length - 1) {
      return true;
    }
  }
  return false;
}

function emailValidate(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapToBool(values, backend ? (s) => backend.emailValidate(s) : emailValidateTs);
}

registerTransform(
  {
    name: "email_validate",
    inputTypes: ["email", "string"],
    autoApply: false,
    priority: 60,
    mode: "series",
  },
  emailValidate,
);

// ---------------------------------------------------------------------------
// Pure-TS single-value exports (cross-surface byte-parity harness)
//
// Bypass the wasm-dispatch wrappers above so a parity test can assert the
// pure-TS path independently of whatever backend is currently registered.
// ---------------------------------------------------------------------------

export {
  emailLowercaseTs,
  emailNormalizeTs,
  emailCanonicalTs,
  emailMaskTs,
  emailExtractDomainTs,
  emailValidateTs,
};
