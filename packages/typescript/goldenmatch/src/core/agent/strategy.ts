/**
 * strategy.ts — Agent decision keystone: profile -> strategy -> config.
 * Edge-safe: no Node.js imports. Ported from goldenmatch/core/agent.py.
 *
 * Task-0 confirmations (pinned so later tasks don't re-derive them):
 *   - `DedupeOptions.config`: a GoldenMatchConfig is passed as
 *     `dedupe(rows, { config })` (option key `config`); see api.ts:33-52.
 *   - `confidence_distribution`: FOUR keys — `auto_merged` / `review` /
 *     `auto_rejected` / `total_pairs` (= result.scoredPairs.length).
 *   - `DomainProfile` (domain.ts:16): exposes `name` + `confidence`
 *     (confidence already = min(1, score/10)). Used directly as
 *     `domain_confidence` for the `> 0.5` branch. NOTE: this differs from
 *     Python's `hits/len(signals)` formula, so only clear-cut domain datasets
 *     are guaranteed to agree (documented Wave-1 caveat).
 *   - `autoConfigureRowsIterate` is async (autoconfig.ts:583) -> the
 *     AgentSession `autoconfigure`/`deduplicate` methods are async.
 */

import type {
  Row,
  GoldenMatchConfig,
  MatchkeyConfig,
  BlockingConfig,
} from "../types.js";
import {
  makeMatchkeyConfig,
  makeMatchkeyField,
  makeBlockingConfig,
} from "../types.js";
import { detectDomain } from "../domain.js";
import type {
  DataProfile,
  FieldProfile,
  StrategyDecision,
  Alternative,
} from "./types.js";

// Column-name patterns that indicate sensitive PII (Python _SENSITIVE_PATTERNS).
const SENSITIVE_PATTERNS = new Set([
  "ssn",
  "social_security",
  "dob",
  "date_of_birth",
  "birth_date",
  "drivers_license",
  "dl_number",
]);

/**
 * Profile rows for strategy selection (port of `profile_for_agent`).
 *
 * For each column computes uniqueness (distinct/row_count), null rate, and
 * average UTF-8 byte length (string columns only). Detects sensitive fields by
 * column-name pattern matching.
 */
export function profileForAgent(rows: readonly Row[]): DataProfile {
  const height = rows.length;
  const cols = height > 0 ? Object.keys(rows[0]!) : [];
  let hasSensitive = false;
  const fields: FieldProfile[] = [];
  const encoder = new TextEncoder();

  for (const col of cols) {
    const colLower = col.toLowerCase().replace(/ /g, "_");
    if (SENSITIVE_PATTERNS.has(colLower)) hasSensitive = true;

    const values = rows.map((r) => r[col]);
    // Polars `null_count` counts true nulls. We also treat undefined / "" as
    // null so empty-string cells don't masquerade as present values.
    const nonNull = values.filter(
      (v) => v !== null && v !== undefined && v !== "",
    );
    const nullCount = height - nonNull.length;
    const distinct = new Set(nonNull.map((v) => String(v))).size;
    const uniqueness = height > 0 ? distinct / height : 0;
    const nullRate = height > 0 ? nullCount / height : 0;

    // Type category: numeric only when every non-null value parses as a number.
    const allNumeric =
      nonNull.length > 0 &&
      nonNull.every(
        (v) => typeof v === "number" || !Number.isNaN(Number(v)),
      );
    const type: FieldProfile["type"] =
      nonNull.length === 0 ? "string" : allNumeric ? "numeric" : "string";

    // Average UTF-8 byte length over non-null string values (Python uses
    // `str.len_bytes()`; TextEncoder().encode(...).length is the edge-safe
    // equivalent — the node-only byte API is intentionally avoided here).
    let avgLength = 0;
    if (type === "string" && nonNull.length > 0) {
      const total = nonNull.reduce(
        (acc, v) => acc + encoder.encode(String(v)).length,
        0,
      );
      avgLength = total / nonNull.length;
    }

    fields.push({
      name: col,
      type,
      uniqueness,
      null_rate: nullRate,
      avg_length: avgLength,
    });
  }

  return { row_count: height, fields, has_sensitive: hasSensitive };
}

// ---------------------------------------------------------------------------
// Strategy selection (port of `select_strategy`)
// ---------------------------------------------------------------------------

/**
 * Choose a matching strategy based on a data profile (port of
 * `select_strategy`).
 *
 * Decision tree (order is load-bearing):
 *   1. Sensitive fields detected -> pprl (manual review, auto_execute=false).
 *   2. Strong IDs only -> exact_only.
 *   3. Strong IDs + fuzzy candidates -> exact_then_fuzzy.
 *   4. Fuzzy candidates available -> fuzzy.
 *   5. Domain detected with confidence > 0.5 -> domain_extraction.
 *   6. Fallback -> fuzzy.
 *
 * Thresholds (exact Python parity):
 *   - strong id: type=string & uniqueness > 0.90 & null_rate < 0.05.
 *   - fuzzy candidate: type=string & uniqueness < 0.90 & avg_length > 3 &
 *     null_rate < 0.50.
 *   - backend: "ray" when row_count > 500_000, else null.
 *
 * Domain confidence uses `detectDomain(colNames).confidence` (the ported
 * DomainProfile.confidence = min(1, score/10)). NOTE: this differs from
 * Python's `hits/len(signals)` formula, so only clear-cut domain datasets are
 * guaranteed to agree on the domain branch (documented Wave-1 caveat).
 */
export function selectStrategy(profile: DataProfile): StrategyDecision {
  // 1. Sensitive data -> PPRL.
  if (profile.has_sensitive) {
    return {
      strategy: "pprl",
      why: "Sensitive fields detected; using privacy-preserving record linkage.",
      domain: null,
      strong_ids: [],
      fuzzy_fields: [],
      backend: null,
      auto_execute: false,
    };
  }

  // Detect domain (best-effort; never throws out of selection).
  let domainName: string | null = null;
  let domainConfidence = 0.0;
  try {
    const colNames = profile.fields.map((f) => f.name);
    const dp = detectDomain(colNames);
    if (dp.name !== "generic" && dp.confidence > 0) {
      domainName = dp.name;
      domainConfidence = dp.confidence;
    }
  } catch {
    /* domain detection is non-fatal */
  }

  // Identify strong IDs and fuzzy candidates.
  const strongIds: string[] = [];
  const fuzzyCandidates: string[] = [];
  for (const f of profile.fields) {
    if (f.type === "string") {
      if (f.uniqueness > 0.9 && f.null_rate < 0.05) {
        strongIds.push(f.name);
      } else if (
        f.uniqueness < 0.9 &&
        f.avg_length > 3 &&
        f.null_rate < 0.5
      ) {
        fuzzyCandidates.push(f.name);
      }
    }
  }

  // Backend recommendation.
  const backend = profile.row_count > 500_000 ? "ray" : null;

  // 2. Strong IDs only.
  if (strongIds.length > 0 && fuzzyCandidates.length === 0) {
    return {
      strategy: "exact_only",
      why: `High-uniqueness fields (${strongIds.join(", ")}) with no fuzzy candidates.`,
      domain: domainName,
      strong_ids: strongIds,
      fuzzy_fields: [],
      backend,
      auto_execute: true,
    };
  }

  // 3. Strong IDs + fuzzy candidates.
  if (strongIds.length > 0 && fuzzyCandidates.length > 0) {
    return {
      strategy: "exact_then_fuzzy",
      why: `Exact on ${strongIds.join(", ")}; fuzzy on ${fuzzyCandidates.join(", ")}.`,
      domain: domainName,
      strong_ids: strongIds,
      fuzzy_fields: fuzzyCandidates,
      backend,
      auto_execute: true,
    };
  }

  // 4. Fuzzy candidates available.
  if (fuzzyCandidates.length > 0) {
    return {
      strategy: "fuzzy",
      why: `Fuzzy matching on ${fuzzyCandidates.join(", ")}.`,
      domain: domainName,
      strong_ids: [],
      fuzzy_fields: fuzzyCandidates,
      backend,
      auto_execute: true,
    };
  }

  // 5. Domain detected with confidence.
  if (domainName !== null && domainConfidence > 0.5) {
    const pct = Math.round(domainConfidence * 100);
    return {
      strategy: "domain_extraction",
      why: `Domain '${domainName}' detected (confidence ${pct}%).`,
      domain: domainName,
      strong_ids: [],
      fuzzy_fields: [],
      backend,
      auto_execute: true,
    };
  }

  // 6. Fallback -> fuzzy over all string fields.
  return {
    strategy: "fuzzy",
    why: "No strong identifiers found; defaulting to fuzzy matching.",
    domain: domainName,
    strong_ids: [],
    fuzzy_fields: profile.fields
      .filter((f) => f.type === "string")
      .map((f) => f.name),
    backend,
    auto_execute: true,
  };
}

// ---------------------------------------------------------------------------
// Alternatives (port of `build_alternatives`)
// ---------------------------------------------------------------------------

/**
 * Generate alternative strategies the user might consider (port of
 * `build_alternatives`). Always offers pprl + fellegi_sunter unless the
 * decision already IS that strategy.
 */
export function buildAlternatives(decision: StrategyDecision): Alternative[] {
  const alts: Alternative[] = [];

  if (decision.strategy !== "pprl") {
    alts.push({
      strategy: "pprl",
      why_not:
        "No sensitive fields detected, but PPRL is available if data leaves your network.",
    });
  }

  if (decision.strategy !== "fellegi_sunter") {
    alts.push({
      strategy: "fellegi_sunter",
      why_not:
        "Probabilistic model available for automatic parameter estimation.",
    });
  }

  return alts;
}

// ---------------------------------------------------------------------------
// Config builder (port of `_decision_to_config`)
// ---------------------------------------------------------------------------

/**
 * Translate a StrategyDecision into a GoldenMatchConfig (port of
 * `_decision_to_config`). Mirrors `buildConfigFromOptions` (api.ts) using the
 * make* factories:
 *   - one exact matchkey per strong id (scorer "exact", transforms
 *     lowercase+strip),
 *   - one weighted matchkey "fuzzy" from the fuzzy fields (scorer
 *     jaro_winkler, weight 1.0, threshold 0.85),
 *   - a placeholder exact "auto" matchkey when neither strong nor fuzzy fields
 *     exist (Python parity),
 *   - blocking ONLY when there are fuzzy fields (static, first fuzzy field,
 *     transforms lowercase+first_token),
 *   - backend propagated from the decision.
 */
export function decisionToConfig(
  decision: StrategyDecision,
): GoldenMatchConfig {
  const matchkeys: MatchkeyConfig[] = [];

  // Exact matchkeys from strong IDs.
  for (const col of decision.strong_ids) {
    matchkeys.push(
      makeMatchkeyConfig({
        name: `exact_${col}`,
        type: "exact",
        fields: [
          makeMatchkeyField({
            field: col,
            transforms: ["lowercase", "strip"],
            scorer: "exact",
            weight: 1.0,
          }),
        ],
      }),
    );
  }

  // Fuzzy matchkey from fuzzy fields (single weighted matchkey named "fuzzy").
  if (decision.fuzzy_fields.length > 0) {
    const fields = decision.fuzzy_fields.map((col) =>
      makeMatchkeyField({
        field: col,
        scorer: "jaro_winkler",
        weight: 1.0,
        transforms: ["lowercase", "strip"],
      }),
    );
    matchkeys.push(
      makeMatchkeyConfig({
        name: "fuzzy",
        type: "weighted",
        threshold: 0.85,
        fields,
      }),
    );
  }

  // Fallback placeholder (Python parity) when nothing matched.
  if (matchkeys.length === 0) {
    matchkeys.push(
      makeMatchkeyConfig({
        name: "auto",
        type: "exact",
        fields: [makeMatchkeyField({ field: "__placeholder__", scorer: "exact" })],
      }),
    );
  }

  // Blocking from the first fuzzy field (only when fuzzy fields exist).
  let blocking: BlockingConfig | undefined;
  if (decision.fuzzy_fields.length > 0) {
    blocking = makeBlockingConfig({
      strategy: "static",
      keys: [
        {
          fields: [decision.fuzzy_fields[0]!],
          transforms: ["lowercase", "first_token"],
        },
      ],
    });
  }

  // Build config without ever spreading `undefined` (exactOptionalPropertyTypes).
  const config: GoldenMatchConfig = {
    matchkeys,
    ...(blocking !== undefined ? { blocking } : {}),
    ...(decision.backend !== null ? { backend: decision.backend } : {}),
  };
  return config;
}
