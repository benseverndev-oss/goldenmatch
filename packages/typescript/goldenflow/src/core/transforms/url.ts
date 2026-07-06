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

// Query-param keys treated as tracking noise for dedup (case-insensitive on the
// KEY). Keep byte-for-byte in lockstep with goldenflow-core's TRACKING_PARAMS
// (url.rs) and the Python fallback (url.py).
const _TRACKING_PARAMS = new Set<string>([
  "utm_source",
  "utm_medium",
  "utm_campaign",
  "utm_term",
  "utm_content",
  "utm_id",
  "utm_name",
  "utm_cid",
  "utm_reader",
  "utm_referrer",
  "utm_social",
  "utm_social_type",
  "gclid",
  "gclsrc",
  "dclid",
  "gbraid",
  "wbraid",
  "fbclid",
  "msclkid",
  "mc_eid",
  "mc_cid",
  "yclid",
  "igshid",
  "twclid",
  "_ga",
  "_gl",
  "ref",
  "ref_src",
  "spm",
]);

/** Drop tracking params from a raw query string; keep the rest in order. */
function stripTrackingQuery(query: string): string {
  return query
    .split("&")
    .filter((p) => !_TRACKING_PARAMS.has((p.split("=", 1)[0] ?? "").toLowerCase()))
    .join("&");
}

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
// url_strip_tracking (series, url|string, 45)
//
// Remove tracking query params (utm_*, gclid, fbclid, ...), preserving the
// rest verbatim (scheme, host case, remaining query order, #fragment). The `?`
// is dropped when no params survive. `undefined` (-> null) for empty input.
// ---------------------------------------------------------------------------

function urlStripTrackingTs(s: string): string | undefined {
  const t = s.trim();
  if (!t) return undefined;
  const hashIdx = t.indexOf("#");
  const main = hashIdx === -1 ? t : t.slice(0, hashIdx);
  const fragment = hashIdx === -1 ? "" : t.slice(hashIdx);
  const qIdx = main.indexOf("?");
  if (qIdx === -1) return main + fragment;
  const prefix = main.slice(0, qIdx);
  const stripped = stripTrackingQuery(main.slice(qIdx + 1));
  return stripped ? `${prefix}?${stripped}${fragment}` : prefix + fragment;
}

function urlStripTracking(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapToStringOrNull(values, backend ? (s) => backend.urlStripTracking(s) : urlStripTrackingTs);
}

registerTransform(
  { name: "url_strip_tracking", inputTypes: ["url", "string"], autoApply: false, priority: 45, mode: "series" },
  urlStripTracking,
);

// ---------------------------------------------------------------------------
// url_strip_www (series, url|string, 45)
//
// Strip a leading `www.` label from the host, preserving scheme, path, and
// host case otherwise. `undefined` (-> null) for empty input.
// ---------------------------------------------------------------------------

function urlStripWwwTs(s: string): string | undefined {
  const t = s.trim();
  if (!t) return undefined;
  const schemeIdx = t.indexOf("://");
  const scheme = schemeIdx === -1 ? "" : t.slice(0, schemeIdx + 3);
  const rest = schemeIdx === -1 ? t : t.slice(schemeIdx + 3);
  const slashIdx = rest.indexOf("/");
  const host = slashIdx === -1 ? rest : rest.slice(0, slashIdx);
  const path = slashIdx === -1 ? "" : rest.slice(slashIdx);
  const stripped = host.slice(0, 4).toLowerCase() === "www." ? host.slice(4) : host;
  return scheme + stripped + path;
}

function urlStripWww(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapToStringOrNull(values, backend ? (s) => backend.urlStripWww(s) : urlStripWwwTs);
}

registerTransform(
  { name: "url_strip_www", inputTypes: ["url", "string"], autoApply: false, priority: 45, mode: "series" },
  urlStripWww,
);

// ---------------------------------------------------------------------------
// url_canonical (series, url|string, 50)
//
// Composite dedup key: ensure scheme, lowercase scheme+host, strip www., drop
// #fragment, remove tracking params, strip trailing slashes. `undefined`
// (-> null) for empty input.
// ---------------------------------------------------------------------------

function urlCanonicalTs(s: string): string | undefined {
  const t = s.trim();
  if (!t) return undefined;
  const hashIdx = t.indexOf("#");
  const main = hashIdx === -1 ? t : t.slice(0, hashIdx);
  const withScheme = _SCHEME_RE.test(main) ? main : "https://" + main;
  const schemeEnd = withScheme.indexOf("://") + 3;
  const scheme = withScheme.slice(0, schemeEnd).toLowerCase();
  const rest = withScheme.slice(schemeEnd);
  const slashIdx = rest.indexOf("/");
  const hostRaw = slashIdx === -1 ? rest : rest.slice(0, slashIdx);
  const path = slashIdx === -1 ? "" : rest.slice(slashIdx);
  let host = hostRaw.toLowerCase();
  if (host.slice(0, 4) === "www.") host = host.slice(4);
  const qIdx = path.indexOf("?");
  const pathpart = (qIdx === -1 ? path : path.slice(0, qIdx)).replace(/\/+$/, "");
  const query = stripTrackingQuery(qIdx === -1 ? "" : path.slice(qIdx + 1));
  return scheme + host + pathpart + (query ? "?" + query : "");
}

function urlCanonical(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapToStringOrNull(values, backend ? (s) => backend.urlCanonical(s) : urlCanonicalTs);
}

registerTransform(
  { name: "url_canonical", inputTypes: ["url", "string"], autoApply: false, priority: 50, mode: "series" },
  urlCanonical,
);

// ---------------------------------------------------------------------------
// Pure-TS single-value exports (cross-surface byte-parity harness)
//
// Bypass the wasm-dispatch wrappers above so a parity test can assert the
// pure-TS path independently of whatever backend is currently registered.
// ---------------------------------------------------------------------------

export { urlNormalizeTs, urlExtractDomainTs, urlStripTrackingTs, urlStripWwwTs, urlCanonicalTs };
