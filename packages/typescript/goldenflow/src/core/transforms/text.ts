/**
 * Text transforms — ported from goldenflow/transforms/text.py
 * Side-effect module: registers 18 text transforms on import.
 */

import type { ColumnValue } from "../types.js";
import { registerTransform } from "./registry.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function mapStrings(
  values: readonly ColumnValue[],
  fn: (s: string) => string,
): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    return fn(v);
  });
}

// ---------------------------------------------------------------------------
// strip (priority 90, auto_apply, string)
// ---------------------------------------------------------------------------

function strip(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => s.trim());
}

registerTransform(
  { name: "strip", inputTypes: ["string"], autoApply: true, priority: 90, mode: "expr" },
  strip,
);

// ---------------------------------------------------------------------------
// lowercase (50, string)
// ---------------------------------------------------------------------------

function lowercase(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => s.toLowerCase());
}

registerTransform(
  { name: "lowercase", inputTypes: ["string"], priority: 50, mode: "expr" },
  lowercase,
);

// ---------------------------------------------------------------------------
// uppercase (50, string)
// ---------------------------------------------------------------------------

function uppercase(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => s.toUpperCase());
}

registerTransform(
  { name: "uppercase", inputTypes: ["string"], priority: 50, mode: "expr" },
  uppercase,
);

// ---------------------------------------------------------------------------
// title_case (50, string)
// ---------------------------------------------------------------------------

function titleCase(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) =>
    s.toLowerCase().replace(/\b\w/g, (ch) => ch.toUpperCase()),
  );
}

registerTransform(
  { name: "title_case", inputTypes: ["string"], priority: 50, mode: "expr" },
  titleCase,
);

// ---------------------------------------------------------------------------
// normalize_unicode (85, auto_apply, string)
// ---------------------------------------------------------------------------

function normalizeUnicode(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) =>
    s.normalize("NFKD").replace(/\p{M}/gu, ""),
  );
}

registerTransform(
  { name: "normalize_unicode", inputTypes: ["string"], autoApply: true, priority: 85, mode: "series" },
  normalizeUnicode,
);

// ---------------------------------------------------------------------------
// remove_punctuation (40, string)
// ---------------------------------------------------------------------------

function removePunctuation(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => s.replace(/[^\w\s]/g, ""));
}

registerTransform(
  { name: "remove_punctuation", inputTypes: ["string"], priority: 40, mode: "series" },
  removePunctuation,
);

// ---------------------------------------------------------------------------
// collapse_whitespace (80, auto_apply, string)
// ---------------------------------------------------------------------------

function collapseWhitespace(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => s.replace(/\s+/g, " ").trim());
}

registerTransform(
  { name: "collapse_whitespace", inputTypes: ["string"], autoApply: true, priority: 80, mode: "expr" },
  collapseWhitespace,
);

// ---------------------------------------------------------------------------
// truncate (30, string, param: n=255)
// ---------------------------------------------------------------------------

function truncate(values: readonly ColumnValue[], n: unknown = 255): ColumnValue[] {
  const maxLen = typeof n === "number" ? n : Number(n) || 255;
  return mapStrings(values, (s) => s.slice(0, maxLen));
}

registerTransform(
  { name: "truncate", inputTypes: ["string"], priority: 30, mode: "series" },
  truncate,
);

// ---------------------------------------------------------------------------
// normalize_quotes (84, auto_apply, string)
// ---------------------------------------------------------------------------

function normalizeQuotes(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) =>
    s
      .replace(/[\u2018\u2019\u201A\u201B]/g, "'")
      .replace(/[\u201C\u201D\u201E\u201F]/g, '"'),
  );
}

registerTransform(
  { name: "normalize_quotes", inputTypes: ["string"], autoApply: true, priority: 84, mode: "series" },
  normalizeQuotes,
);

// ---------------------------------------------------------------------------
// remove_html_tags (45, string)
// ---------------------------------------------------------------------------

function removeHtmlTags(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => {
    // Single-pass `<[^>]*>` removal lets nested-tag payloads like
    // `<<script>script>` collapse to `<script>` after one rewrite.
    // Loop until the output stabilises so the transform is idempotent
    // against adversarial input. Bounded by string length -- each pass
    // strictly shrinks the input (or matches zero, ending the loop).
    let prev: string;
    let out = s;
    do {
      prev = out;
      out = out.replace(/<[^>]*>/g, "");
    } while (out !== prev);
    return out;
  });
}

registerTransform(
  { name: "remove_html_tags", inputTypes: ["string"], priority: 45, mode: "series" },
  removeHtmlTags,
);

// ---------------------------------------------------------------------------
// remove_urls (40, string)
// ---------------------------------------------------------------------------

function removeUrls(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) =>
    s.replace(/https?:\/\/[^\s]+/g, "").trim(),
  );
}

registerTransform(
  { name: "remove_urls", inputTypes: ["string"], priority: 40, mode: "series" },
  removeUrls,
);

// ---------------------------------------------------------------------------
// remove_digits (35, string)
// ---------------------------------------------------------------------------

function removeDigits(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => s.replace(/\d/g, ""));
}

registerTransform(
  { name: "remove_digits", inputTypes: ["string"], priority: 35, mode: "series" },
  removeDigits,
);

// ---------------------------------------------------------------------------
// pad_left (30, string, params: width=10, char="0")
// ---------------------------------------------------------------------------

function padLeft(
  values: readonly ColumnValue[],
  width: unknown = 10,
  char: unknown = "0",
): ColumnValue[] {
  const w = typeof width === "number" ? width : Number(width) || 10;
  const c = typeof char === "string" ? char : "0";
  return mapStrings(values, (s) => s.padStart(w, c));
}

registerTransform(
  { name: "pad_left", inputTypes: ["string"], priority: 30, mode: "series" },
  padLeft,
);

// ---------------------------------------------------------------------------
// pad_right (30, string, params: width=10, char=" ")
// ---------------------------------------------------------------------------

function padRight(
  values: readonly ColumnValue[],
  width: unknown = 10,
  char: unknown = " ",
): ColumnValue[] {
  const w = typeof width === "number" ? width : Number(width) || 10;
  const c = typeof char === "string" ? char : " ";
  return mapStrings(values, (s) => s.padEnd(w, c));
}

registerTransform(
  { name: "pad_right", inputTypes: ["string"], priority: 30, mode: "series" },
  padRight,
);

// ---------------------------------------------------------------------------
// remove_emojis (38, string)
// ---------------------------------------------------------------------------

function removeEmojis(values: readonly ColumnValue[]): ColumnValue[] {
  // Covers common emoji Unicode ranges including emoticons, symbols, dingbats,
  // supplemental symbols, flags, and extended pictographic.
  const emojiPattern =
    /[\u{1F600}-\u{1F64F}\u{1F300}-\u{1F5FF}\u{1F680}-\u{1F6FF}\u{1F1E0}-\u{1F1FF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}\u{FE00}-\u{FE0F}\u{1F900}-\u{1F9FF}\u{1FA00}-\u{1FA6F}\u{1FA70}-\u{1FAFF}\u{200D}\u{20E3}\u{E0020}-\u{E007F}]/gu;
  return mapStrings(values, (s) => s.replace(emojiPattern, ""));
}

registerTransform(
  { name: "remove_emojis", inputTypes: ["string"], priority: 38, mode: "series" },
  removeEmojis,
);

// ---------------------------------------------------------------------------
// fix_mojibake (86, string)
// ---------------------------------------------------------------------------

function fixMojibake(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => {
    try {
      // Attempt latin1 -> utf8 re-encoding: encode string bytes as latin1,
      // then decode as UTF-8. If the result is valid and different, use it.
      const encoder = new TextEncoder();
      const bytes = new Uint8Array(s.length);
      for (let i = 0; i < s.length; i++) {
        const code = s.charCodeAt(i);
        if (code > 255) return s; // Not latin1-encodable; skip
        bytes[i] = code;
      }
      const decoded = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      return decoded;
    } catch {
      // If decoding fails the string is not mojibake; return as-is
      return s;
    }
  });
}

registerTransform(
  { name: "fix_mojibake", inputTypes: ["string"], priority: 86, mode: "series" },
  fixMojibake,
);

// ---------------------------------------------------------------------------
// normalize_line_endings (82, string)
// ---------------------------------------------------------------------------

function normalizeLineEndings(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => s.replace(/\r\n/g, "\n").replace(/\r/g, "\n"));
}

registerTransform(
  { name: "normalize_line_endings", inputTypes: ["string"], priority: 82, mode: "series" },
  normalizeLineEndings,
);

// ---------------------------------------------------------------------------
// extract_numbers (30, string)
// ---------------------------------------------------------------------------

function extractNumbers(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => {
    const nums = s.match(/-?\d+(?:\.\d+)?/g);
    return nums ? nums.join(" ") : "";
  });
}

registerTransform(
  { name: "extract_numbers", inputTypes: ["string"], priority: 30, mode: "series" },
  extractNumbers,
);
