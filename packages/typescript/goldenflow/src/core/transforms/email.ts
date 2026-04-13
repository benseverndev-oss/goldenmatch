/**
 * Email transforms — ported from goldenflow/transforms/email.py
 * Side-effect module: registers 4 email transforms on import.
 */

import type { ColumnValue } from "../types.js";
import { registerTransform } from "./registry.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Simple but practical email regex (RFC 5321 simplified). */
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** Gmail-like domains where dots in the local part are ignored. */
const GMAIL_DOMAINS = new Set(["gmail.com", "googlemail.com"]);

// ---------------------------------------------------------------------------
// email_lowercase (55, email|string)
// ---------------------------------------------------------------------------

function emailLowercase(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    return v.toLowerCase();
  });
}

registerTransform(
  { name: "email_lowercase", inputTypes: ["email", "string"], priority: 55, mode: "series" },
  emailLowercase,
);

// ---------------------------------------------------------------------------
// email_normalize (50, email)
// ---------------------------------------------------------------------------

function emailNormalize(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    const lowered = v.toLowerCase().trim();
    const atIdx = lowered.lastIndexOf("@");
    if (atIdx === -1) return lowered;

    let local = lowered.slice(0, atIdx);
    const domain = lowered.slice(atIdx + 1);

    // Strip +tags (e.g. user+tag@gmail.com -> user@gmail.com)
    const plusIdx = local.indexOf("+");
    if (plusIdx !== -1) {
      local = local.slice(0, plusIdx);
    }

    // Strip dots for Gmail-like domains
    if (GMAIL_DOMAINS.has(domain)) {
      local = local.replace(/\./g, "");
    }

    return `${local}@${domain}`;
  });
}

registerTransform(
  { name: "email_normalize", inputTypes: ["email"], priority: 50, mode: "series" },
  emailNormalize,
);

// ---------------------------------------------------------------------------
// email_extract_domain (40, email)
// ---------------------------------------------------------------------------

function emailExtractDomain(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    const atIdx = v.lastIndexOf("@");
    if (atIdx === -1) return null;
    return v.slice(atIdx + 1).toLowerCase();
  });
}

registerTransform(
  { name: "email_extract_domain", inputTypes: ["email"], priority: 40, mode: "series" },
  emailExtractDomain,
);

// ---------------------------------------------------------------------------
// email_validate (60, email|string)
// ---------------------------------------------------------------------------

function emailValidate(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    return EMAIL_RE.test(v.trim());
  });
}

registerTransform(
  { name: "email_validate", inputTypes: ["email", "string"], priority: 60, mode: "series" },
  emailValidate,
);
