/**
 * Company/organization transforms — ported from
 * goldenflow/transforms/company.py.
 * Side-effect module: registers 3 company transforms on import.
 *
 * Owned-kernel family: each transform is a byte-for-byte port of the Python
 * pure-TS reference (`_company_normalize_py` et al.), itself proven
 * byte-identical to the Rust `goldenflow-core::company` kernels (parity corpus
 * in `tests/parity/identifiers_corpus.jsonl`). Each transform dispatches to the
 * opt-in WASM backend when `enableWasm()` has succeeded; otherwise it runs the
 * pure-TS implementation below. Pure-TS is the default.
 */

import type { ColumnValue } from "../types.js";
import { registerTransform } from "./registry.js";
import { getFlowWasmBackend, type FlowWasmBackend } from "../wasm/backend.js";

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

// Legal-form suffix tokens (lowercase, punctuation-free). Keep byte-for-byte in
// lockstep with goldenflow-core's LEGAL_TOKENS (company.rs) and the Python
// fallback (company.py).
const _LEGAL_TOKENS = new Set<string>([
  "inc", "incorporated", "llc", "llp", "lp", "ltd", "limited",
  "corp", "corporation", "co", "company", "companies", "gmbh", "ag",
  "sa", "ab", "plc", "pc", "pllc", "nv", "bv", "oy", "oyj", "asa",
  "kg", "kgaa", "srl", "spa", "pty", "sarl", "aps", "kk", "sas", "sl",
  "sro", "doo", "pvt", "bhd", "sdn", "ulc",
]);

function isAsciiAlnum(c: string): boolean {
  return (c >= "a" && c <= "z") || (c >= "A" && c <= "Z") || (c >= "0" && c <= "9");
}

/** Alnum-only, lowercased comparison key for a token (`L.L.C.` -> `llc`). */
function legalKey(tok: string): string {
  let out = "";
  for (const c of tok) if (isAsciiAlnum(c)) out += c.toLowerCase();
  return out;
}

function isLegal(tok: string): boolean {
  const key = legalKey(tok);
  return key !== "" && _LEGAL_TOKENS.has(key);
}

/** Index of the last whitespace char, or -1 (Rust `rfind(char::is_whitespace)`). */
function lastWs(s: string): number {
  for (let i = s.length - 1; i >= 0; i--) if (/\s/.test(s[i]!)) return i;
  return -1;
}

function companyNormalizeTs(val: string): string | undefined {
  const trimmed = val.trim();
  if (!trimmed) return undefined;
  const lower = trimmed.toLowerCase();
  // Keep ASCII alnum + '&'; DROP '.' (acronym-preserving); else word break.
  let cleaned = "";
  for (const c of lower) {
    if (isAsciiAlnum(c) || c === "&") cleaned += c;
    else if (c !== ".") cleaned += " ";
  }
  const tokens = cleaned.split(/\s+/).filter(Boolean);
  if (tokens[0] === "the") tokens.shift();
  while (tokens.length > 0 && (tokens[tokens.length - 1] === "&" || isLegal(tokens[tokens.length - 1]!))) {
    tokens.pop();
  }
  return tokens.join(" ");
}

function companyStripLegalTs(val: string): string | undefined {
  const trimmed = val.trim();
  if (!trimmed) return undefined;
  let t = trimmed;
  for (;;) {
    const core = t.replace(/[\s.,]+$/, "");
    const idx = lastWs(core);
    const head = idx === -1 ? "" : core.slice(0, idx);
    const candidate = idx === -1 ? core : core.slice(idx + 1);
    if (isLegal(candidate)) {
      t = head;
    } else {
      t = core;
      break;
    }
  }
  return t.trim();
}

function companyExtractLegalTs(val: string): string | undefined {
  const core = val.trim().replace(/[\s.,]+$/, "");
  if (!core) return undefined;
  const idx = lastWs(core);
  const last = idx === -1 ? core : core.slice(idx + 1);
  const key = legalKey(last);
  return _LEGAL_TOKENS.has(key) ? key : undefined;
}

function companyNormalize(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapToStringOrNull(values, backend ? (s) => backend.companyNormalize(s) : companyNormalizeTs);
}

function companyStripLegal(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapToStringOrNull(values, backend ? (s) => backend.companyStripLegal(s) : companyStripLegalTs);
}

function companyExtractLegal(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapToStringOrNull(values, backend ? (s) => backend.companyExtractLegal(s) : companyExtractLegalTs);
}

registerTransform(
  { name: "company_normalize", inputTypes: ["company", "organization", "string"], autoApply: false, priority: 50, mode: "series" },
  companyNormalize,
);
registerTransform(
  { name: "company_strip_legal", inputTypes: ["company", "organization", "string"], autoApply: false, priority: 45, mode: "series" },
  companyStripLegal,
);
registerTransform(
  { name: "company_extract_legal", inputTypes: ["company", "organization", "string"], autoApply: false, priority: 40, mode: "series" },
  companyExtractLegal,
);

export { companyNormalizeTs, companyStripLegalTs, companyExtractLegalTs };
