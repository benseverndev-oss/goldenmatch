/**
 * Encoding detection profiler — detects encoding anomalies in string columns.
 * Port of goldencheck/profilers/encoding_detection.py.
 */

import type { TabularData } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { Profiler } from "./base.js";

// Zero-width Unicode characters
const ZERO_WIDTH_RE = /[\u200B\u200C\u200D\uFEFF]/;

// Smart/curly quotes
const SMART_QUOTES_RE = /[\u2018\u2019\u201C\u201D]/;

// Non-ASCII characters (U+0080+)
const NON_ASCII_RE = /[^\x00-\x7F]/;

// Control characters (non-printable, excluding tab/newline/CR)
const CONTROL_CHAR_RE = /[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/;

export class EncodingDetectionProfiler implements Profiler {
  profile(data: TabularData, column: string): Finding[] {
    const findings: Finding[] = [];

    if (!data.isString(column)) return findings;

    const nonNull = data.stringValues(column);
    if (nonNull.length === 0) return findings;

    // 1. Zero-width Unicode characters
    const zwMatches = nonNull.filter((v) => ZERO_WIDTH_RE.test(v));
    if (zwMatches.length > 0) {
      findings.push(
        makeFinding({
          severity: Severity.WARNING,
          column,
          check: "encoding_detection",
          message: `${zwMatches.length} value(s) contain zero-width unicode characters (U+200B/U+200C/U+200D/U+FEFF) — likely encoding artifact`,
          affectedRows: zwMatches.length,
          sampleValues: zwMatches.slice(0, 5).map((v) => JSON.stringify(v)),
          suggestion: "Strip zero-width characters from this column",
          confidence: 0.8,
        }),
      );
    }

    // 2. Smart/curly quotes
    const sqMatches = nonNull.filter((v) => SMART_QUOTES_RE.test(v));
    if (sqMatches.length > 0) {
      findings.push(
        makeFinding({
          severity: Severity.INFO,
          column,
          check: "encoding_detection",
          message: `${sqMatches.length} value(s) contain smart quote / curly quote characters (\u2018\u2019\u201C\u201D) — may be encoding inconsistency`,
          affectedRows: sqMatches.length,
          sampleValues: sqMatches.slice(0, 5).map((v) => JSON.stringify(v)),
          suggestion: "Normalise smart quotes to straight quotes if encoding consistency is required",
          confidence: 0.6,
        }),
      );
    }

    // 3. Non-ASCII characters
    const naMatches = nonNull.filter((v) => NON_ASCII_RE.test(v));
    if (naMatches.length > 0) {
      findings.push(
        makeFinding({
          severity: Severity.INFO,
          column,
          check: "encoding_detection",
          message: `${naMatches.length} value(s) contain non-ASCII / unicode characters — verify encoding is intentional (international text vs. mojibake)`,
          affectedRows: naMatches.length,
          sampleValues: naMatches.slice(0, 5).map((v) => JSON.stringify(v)),
          suggestion: "Confirm the source encoding; if mojibake, re-encode from Latin-1 to UTF-8",
          confidence: 0.5,
        }),
      );
    }

    // 4. Control characters
    const ctrlMatches = nonNull.filter((v) => CONTROL_CHAR_RE.test(v));
    if (ctrlMatches.length > 0) {
      findings.push(
        makeFinding({
          severity: Severity.WARNING,
          column,
          check: "encoding_detection",
          message: `${ctrlMatches.length} value(s) contain non-printable control characters — likely encoding or data extraction issue`,
          affectedRows: ctrlMatches.length,
          sampleValues: ctrlMatches.slice(0, 5).map((v) => JSON.stringify(v)),
          suggestion: "Strip or replace control characters",
          confidence: 0.8,
        }),
      );
    }

    return findings;
  }
}
