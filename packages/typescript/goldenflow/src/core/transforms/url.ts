/**
 * URL transforms — ported from goldenflow/transforms/url.py
 * Side-effect module: registers 2 URL transforms on import.
 */

import type { ColumnValue } from "../types.js";
import { registerTransform } from "./registry.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function mapStrings(
  values: readonly ColumnValue[],
  fn: (s: string) => string | null,
): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    return fn(v);
  });
}

const _SCHEME_RE = /^https?:\/\//i;

// ---------------------------------------------------------------------------
// url_normalize (series, url|string, 50)
// ---------------------------------------------------------------------------

function urlNormalize(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => {
    let val = s.trim();
    if (!val) return null;

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

    // Strip trailing slash
    let result = scheme + domain + path;
    if (result.endsWith("/") && result.length > schemeEnd + domain.length + 1) {
      result = result.replace(/\/+$/, "");
    } else if (result.endsWith("/") && path === "/") {
      result = result.slice(0, -1);
    }

    return result;
  });
}

registerTransform(
  { name: "url_normalize", inputTypes: ["url", "string"], priority: 50, mode: "series" },
  urlNormalize,
);

// ---------------------------------------------------------------------------
// url_extract_domain (series, url|string, 40)
// ---------------------------------------------------------------------------

function urlExtractDomain(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => {
    let val = s.trim();
    if (!val) return null;

    // Strip scheme
    if (val.includes("://")) {
      val = val.split("://", 2)[1]!;
    }

    // Take everything before the first /
    const domain = val.split("/", 1)[0]!;
    return domain ? domain.toLowerCase() : null;
  });
}

registerTransform(
  { name: "url_extract_domain", inputTypes: ["url", "string"], priority: 40, mode: "series" },
  urlExtractDomain,
);
