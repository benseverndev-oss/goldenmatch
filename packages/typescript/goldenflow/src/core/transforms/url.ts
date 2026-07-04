/**
 * URL transforms — ported from goldenflow/transforms/url.py
 * Side-effect module: registers 2 URL transforms on import.
 *
 * Owned-kernel family (D2 wave): each transform is a byte-for-byte port of
 * the Python pure-TS reference (`_url_normalize_py` et al. in
 * `goldenflow/transforms/url.py`), which is itself proven byte-identical to
 * the Rust `goldenflow-core::url` kernels (parity corpus in
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

const _SCHEME_RE = /^https?:\/\//i;

// ---------------------------------------------------------------------------
// url_normalize (series, url|string, 50)
//
// Pure-TS reference for goldenflow-core's `url::url_normalize` kernel. Ensure
// a scheme, lowercase the scheme + domain, keep the path as-is, and strip a
// trailing slash (unless the path IS just "/", in which case exactly one
// trailing slash is dropped). `undefined` (-> null) for empty (post-trim)
// input.
// ---------------------------------------------------------------------------

function urlNormalizeTs(s: string): string | undefined {
  let val = s.trim();
  if (!val) return undefined;

  // Add scheme if missing
  if (!_SCHEME_RE.test(val)) {
    val = "https://" + val;
  }

  // Split scheme from rest
  const schemeEnd = val.indexOf("://") + 3;
  const scheme = val.slice(0, schemeEnd).toLowerCase();
  const rest = val.slice(schemeEnd);

  // Lowercase the domain (everything before first /)
  const slashIdx = rest.indexOf("/");
  let domain: string;
  let path: string;
  if (slashIdx === -1) {
    domain = rest.toLowerCase();
    path = "";
  } else {
    domain = rest.slice(0, slashIdx).toLowerCase();
    path = rest.slice(slashIdx);
  }

  // Strip trailing slash (but not if path is just "/")
  let result = scheme + domain + path;
  if (result.endsWith("/") && result.length > schemeEnd + domain.length + 1) {
    result = result.replace(/\/+$/, "");
  } else if (result.endsWith("/") && path === "/") {
    result = result.slice(0, -1);
  }

  return result;
}

function urlNormalize(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapToStringOrNull(values, backend ? (s) => backend.urlNormalize(s) : urlNormalizeTs);
}

registerTransform(
  {
    name: "url_normalize",
    inputTypes: ["url", "string"],
    autoApply: false,
    priority: 50,
    mode: "series",
  },
  urlNormalize,
);

// ---------------------------------------------------------------------------
// url_extract_domain (series, url|string, 40)
//
// Pure-TS reference for goldenflow-core's `url::url_extract_domain` kernel.
// Strip an optional `scheme://` prefix, then take everything before the
// first `/`. `undefined` (-> null) for empty (post-trim) input or an empty
// domain.
// ---------------------------------------------------------------------------

function urlExtractDomainTs(s: string): string | undefined {
  let val = s.trim();
  if (!val) return undefined;

  // Strip scheme
  const schemeIdx = val.indexOf("://");
  if (schemeIdx !== -1) {
    val = val.slice(schemeIdx + 3);
  }

  // Take everything before the first /
  const domain = val.split("/", 1)[0]!;
  return domain ? domain.toLowerCase() : undefined;
}

function urlExtractDomain(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapToStringOrNull(
    values,
    backend ? (s) => backend.urlExtractDomain(s) : urlExtractDomainTs,
  );
}

registerTransform(
  {
    name: "url_extract_domain",
    inputTypes: ["url", "string"],
    autoApply: false,
    priority: 40,
    mode: "series",
  },
  urlExtractDomain,
);

// ---------------------------------------------------------------------------
// Pure-TS single-value exports (cross-surface byte-parity harness)
//
// Bypass the wasm-dispatch wrappers above so a parity test can assert the
// pure-TS path independently of whatever backend is currently registered.
// ---------------------------------------------------------------------------

export { urlNormalizeTs, urlExtractDomainTs };
