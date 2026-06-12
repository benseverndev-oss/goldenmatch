/**
 * Freshness / staleness profiler for date & datetime columns.
 * Port of goldencheck/profilers/freshness.py (pure-JS; no native kernel).
 *
 * - future_dated (WARNING, always on): values after "now" — clock skew / typos.
 * - stale_data (INFO, name-gated): newest value on an update/event column is
 *   more than STALE_DAYS old.
 *
 * Dates arrive as ISO strings in TabularData (data.dtype() reports "date" /
 * "datetime" via ISO regex), so we parse with Date.parse and compare epoch-ms.
 */
import type { TabularData } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { Profiler } from "./base.js";

const STALE_DAYS = 365;
const MS_PER_DAY = 86_400_000;

// Column-name signals that the timestamp tracks "last change", so old == stale.
const UPDATE_KEYWORDS = [
  "updated", "modified", "last_seen", "lastseen", "last_login", "lastlogin",
  "ingested", "loaded", "refreshed", "synced", "as_of", "asof", "event",
  "timestamp", "created", "inserted",
];

function looksLikeUpdateColumn(name: string): boolean {
  const lower = name.toLowerCase();
  return UPDATE_KEYWORDS.some((kw) => lower.includes(kw));
}

export class FreshnessProfiler implements Profiler {
  profile(data: TabularData, column: string): Finding[] {
    const dt = data.dtype(column);
    if (dt !== "date" && dt !== "datetime") return [];

    const nonNull = data.dropNulls(column);
    if (nonNull.length === 0) return [];

    const now = Date.now();
    let futureCount = 0;
    let newestMs = -Infinity;
    let newestRaw: string | null = null;
    for (const v of nonNull) {
      const ms = Date.parse(String(v));
      if (!Number.isFinite(ms)) continue;
      if (ms > now) futureCount++;
      if (ms > newestMs) {
        newestMs = ms;
        newestRaw = String(v);
      }
    }
    if (newestRaw === null) return [];

    const findings: Finding[] = [];

    if (futureCount > 0) {
      findings.push(
        makeFinding({
          severity: Severity.WARNING,
          column,
          check: "future_dated",
          message:
            `${futureCount} value(s) in '${column}' are in the future ` +
            `(newest: ${newestRaw}) — likely clock skew or a data-entry error.`,
          affectedRows: futureCount,
          sampleValues: [newestRaw],
          suggestion:
            "Verify the source clock/timezone, or treat future-dated rows as invalid.",
          confidence: 0.7,
          metadata: { technique: "freshness", future_count: futureCount },
        }),
      );
    }

    if (looksLikeUpdateColumn(column)) {
      const ageDays = Math.floor((now - newestMs) / MS_PER_DAY);
      if (ageDays > STALE_DAYS) {
        findings.push(
          makeFinding({
            severity: Severity.INFO,
            column,
            check: "stale_data",
            message:
              `Newest '${column}' is ${ageDays} days old (${newestRaw}) — ` +
              `this update/event timestamp suggests the data may be stale.`,
            affectedRows: nonNull.length,
            sampleValues: [newestRaw],
            suggestion: "Confirm the pipeline feeding this table is still running.",
            confidence: 0.5,
            metadata: { technique: "freshness", age_days: ageDays },
          }),
        );
      }
    }

    return findings;
  }
}
