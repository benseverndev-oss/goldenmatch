/**
 * autoconfigRules.ts — Default rule table for HeuristicRefitPolicy.
 *
 * Port of Python ``goldenmatch/core/autoconfig_rules.py`` (v1.7/v1.8 base
 * rules only). Indicator-aware rules and negative-evidence rules are
 * intentionally deferred to Wave 2 / Wave 3.
 *
 * Edge-safe: no `node:` imports.
 *
 * ## Rules ported in Wave 1 (v1.7 / v1.8)
 *
 *  1. ``ruleBlockingSingletonTrap`` — candidates_compared == 0 with n_blocks > 0
 *  2. ``ruleBlockingTooCoarse`` — block_sizes_p99 > 10 * avg (skewed)
 *  3. ``ruleBlockingKeySwap`` — mass_above == 0 after a prior decision
 *  4. ``ruleLowReductionRatio`` — reduction_ratio < 0.5 (add soundex multi-pass)
 *  5. ``ruleLowTransitivity`` — transitivity < 0.85 (lower threshold by 0.05)
 *  6. ``ruleNoMatches`` — mass_above == 0 (lower threshold by 0.05)
 *  7. ``ruleUnimodalScoring`` — dip_statistic < 0.01 (swap scorer → ensemble)
 *
 * ## Intentionally NOT ported in Wave 1
 *
 *  - ``rule_blocking_field_null_heavy`` (Wave 2 helper)
 *  - ``rule_uniform_heavy_blocking`` (Wave 2 helper)
 *  - ``rule_recall_gap_suspected`` (relies on random_pair_above_threshold_rate
 *    probe which the TS controller does not yet emit — Wave 2)
 *  - ``rule_corruption_normalize`` (Wave 2, needs IndicatorContext)
 *  - ``rule_cross_blocking_disagreement`` (Wave 2, needs IndicatorContext)
 *  - ``rule_sparse_match_expand`` (Wave 2, needs IndicatorContext)
 *  - ``rule_demote_clustered_identity`` (Wave 3, needs CollisionSignal)
 *  - ``rule_enable_llm_scorer`` — handled post-iteration in Python
 *    via ``_maybe_decorate_with_llm_scorer``; deliberately NOT in the
 *    default rule table even on the Python side.
 */

import type {
  GoldenMatchConfig,
  MatchkeyConfig,
  MatchkeyField,
  WeightedMatchkey,
  BlockingKeyConfig,
} from "./types.js";
import { getMatchkeys } from "./types.js";
import type { Rule } from "./autoconfigPolicy.js";
import type { PolicyDecision } from "./autoconfigHistory.js";
import { HealthVerdict, complexityHealth } from "./complexityProfile.js";

const THRESHOLD_FLOOR = 0.5;
const COLLISION_RATE_THRESHOLD = 0.75;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function firstWeightedMatchkey(cfg: GoldenMatchConfig): WeightedMatchkey | null {
  for (const mk of getMatchkeys(cfg)) {
    if (mk.type === "weighted") return mk;
  }
  return null;
}

function isDerived(fieldName: string): boolean {
  return fieldName.startsWith("__");
}

function existingBlockingFields(cfg: GoldenMatchConfig): Set<string> {
  const fields = new Set<string>();
  const bk = cfg.blocking;
  if (!bk) return fields;
  for (const k of bk.keys ?? []) for (const f of k.fields ?? []) fields.add(f);
  for (const k of bk.passes ?? []) for (const f of k.fields ?? []) fields.add(f);
  return fields;
}

function replaceMatchkey(
  cfg: GoldenMatchConfig,
  oldMk: MatchkeyConfig,
  newMk: MatchkeyConfig,
): GoldenMatchConfig {
  const mks = getMatchkeys(cfg).map((m) => (m === oldMk ? newMk : m));
  return { ...cfg, matchkeys: mks };
}

// ---------------------------------------------------------------------------
// Rule 1 — blocking_singleton_trap
// ---------------------------------------------------------------------------

export const ruleBlockingSingletonTrap: Rule = (ctx) => {
  const { profile, config } = ctx;
  const bp = profile.blocking;
  const sp = profile.scoring;
  if (sp.candidatesCompared > 0) return null;
  if (bp.nBlocks === 0) return null;
  if (!config.blocking) return null;

  const mk = firstWeightedMatchkey(config);
  if (!mk) return null;
  let targetField: string | null = null;
  for (const f of mk.fields) {
    const ct = profile.data.columnTypes[f.field] ?? "unknown";
    if (ct === "text" || ct === "name") {
      targetField = f.field;
      break;
    }
  }
  if (targetField === null) return null;

  const newBlocking = {
    ...config.blocking,
    strategy: "static" as const,
    keys: [
      {
        fields: [targetField],
        transforms: ["lowercase", "first_token"],
      } satisfies BlockingKeyConfig,
    ],
  };
  const newCfg: GoldenMatchConfig = { ...config, blocking: newBlocking };
  const singletonPct =
    bp.nBlocks > 0 ? bp.singletonBlockCount / bp.nBlocks : 0;
  const decision: PolicyDecision = {
    ruleName: "blocking_singleton_trap",
    rationale: `candidates_compared=0 with n_blocks=${bp.nBlocks}; singletons=${bp.singletonBlockCount}/${bp.nBlocks} (${(singletonPct * 100).toFixed(0)}%); switching blocking to first_token('${targetField}')`,
    configDiff: {
      "blocking.keys[0].fields": [targetField],
      "blocking.keys[0].transforms": ["lowercase", "first_token"],
    },
  };
  return [newCfg, decision];
};

// ---------------------------------------------------------------------------
// Rule 2 — blocking_too_coarse
// ---------------------------------------------------------------------------

export const ruleBlockingTooCoarse: Rule = (ctx) => {
  const { profile, config } = ctx;
  const bp = profile.blocking;
  const nRows = profile.data.nRows;
  if (bp.nBlocks === 0 || nRows === 0) return null;
  const avg = nRows / bp.nBlocks;
  if (bp.blockSizesP99 <= 10 * avg) return null;
  if (!config.blocking) return null;
  const used = existingBlockingFields(config);
  let newCol: string | null = null;
  for (const [col, ratio] of Object.entries(profile.data.cardinalityRatio)) {
    if (ratio >= 0.01 && ratio <= 0.95 && !used.has(col)) {
      newCol = col;
      break;
    }
  }
  if (newCol === null) return null;
  const newBlocking = {
    ...config.blocking,
    keys: [{ fields: [newCol], transforms: ["lowercase"] } satisfies BlockingKeyConfig],
  };
  const newCfg: GoldenMatchConfig = { ...config, blocking: newBlocking };
  const ratio = profile.data.cardinalityRatio[newCol] ?? 0;
  const decision: PolicyDecision = {
    ruleName: "blocking_too_coarse",
    rationale: `block_sizes_p99=${bp.blockSizesP99} > 10 * avg (${(10 * avg).toFixed(0)}); trying more selective key '${newCol}' (cardinality ${ratio.toFixed(2)})`,
    configDiff: { "blocking.keys[0].fields": [newCol] },
  };
  return [newCfg, decision];
};

// ---------------------------------------------------------------------------
// Rule 3 — blocking_key_swap (iter-1+ fallback)
// ---------------------------------------------------------------------------

export const ruleBlockingKeySwap: Rule = (ctx) => {
  const { profile, config, history } = ctx;
  const sp = profile.scoring;
  if (sp.candidatesCompared === 0) return null;
  if (sp.massAboveThreshold > 0) return null;
  if (history.decisions.length === 0) return null;
  if (!config.blocking) return null;

  const mk = firstWeightedMatchkey(config);
  if (!mk) return null;
  let targetField: string | null = null;
  for (const f of mk.fields) {
    const ct = profile.data.columnTypes[f.field] ?? "unknown";
    if (ct === "text" || ct === "name") {
      targetField = f.field;
      break;
    }
  }
  if (targetField === null) return null;

  // Anti-oscillation: don't propose what we already have.
  const existingFirst = config.blocking.keys[0];
  if (
    existingFirst &&
    existingFirst.fields.length === 1 &&
    existingFirst.fields[0] === targetField &&
    existingFirst.transforms.includes("first_token")
  ) {
    return null;
  }

  const newBlocking = {
    ...config.blocking,
    strategy: "static" as const,
    keys: [
      { fields: [targetField], transforms: ["lowercase", "first_token"] } satisfies BlockingKeyConfig,
    ],
  };

  // Drop exact matchkeys whose fields are ENTIRELY derived (__*).
  const surviving: MatchkeyConfig[] = [];
  const dropped: string[] = [];
  for (const m of getMatchkeys(config)) {
    if (m.type === "exact" && m.fields.length > 0) {
      const allDerived = m.fields.every((f) => isDerived(f.field));
      if (allDerived) {
        dropped.push(m.name);
        continue;
      }
    }
    surviving.push(m);
  }

  const newCfg: GoldenMatchConfig = {
    ...config,
    blocking: newBlocking,
    ...(dropped.length > 0 ? { matchkeys: surviving } : {}),
  };
  const ratParts = [
    `after ${history.decisions.length} prior decision(s), candidates_compared=${sp.candidatesCompared} but mass_above_threshold=${sp.massAboveThreshold}; swapping blocking to first_token('${targetField}')`,
  ];
  if (dropped.length > 0) {
    ratParts.push(
      `; dropped ${dropped.length} stale derived-column exact matchkey(s): ${JSON.stringify(dropped)}`,
    );
  }
  const decision: PolicyDecision = {
    ruleName: "blocking_key_swap",
    rationale: ratParts.join(""),
    configDiff: {
      "blocking.keys[0].fields": [targetField],
      "blocking.keys[0].transforms": ["lowercase", "first_token"],
      ...(dropped.length > 0 ? { "matchkeys.dropped": dropped } : {}),
    },
  };
  return [newCfg, decision];
};

// ---------------------------------------------------------------------------
// Rule 4 — low_reduction_ratio
// ---------------------------------------------------------------------------

export const ruleLowReductionRatio: Rule = (ctx) => {
  const { profile, config } = ctx;
  const bp = profile.blocking;
  if (bp.reductionRatio >= 0.5) return null;
  if (!config.blocking || config.blocking.keys.length === 0) return null;
  const textCols: string[] = [];
  for (const [col, ct] of Object.entries(profile.data.columnTypes)) {
    if (ct === "text" || ct === "name") textCols.push(col);
  }
  if (textCols.length === 0) return null;
  const used = existingBlockingFields(config);
  const candidate = textCols.find((c) => !used.has(c)) ?? textCols[0]!;

  const existingKeys = Array.from(config.blocking.keys);
  const newPass: BlockingKeyConfig = {
    fields: [candidate],
    transforms: ["soundex"],
  };
  const newBlocking = {
    ...config.blocking,
    strategy: "multi_pass" as const,
    passes: [...existingKeys, newPass],
  };
  const newCfg: GoldenMatchConfig = { ...config, blocking: newBlocking };
  const decision: PolicyDecision = {
    ruleName: "low_reduction_ratio",
    rationale: `reduction_ratio=${bp.reductionRatio.toFixed(2)} < 0.5; adding soundex pass on '${candidate}'`,
    configDiff: { "blocking.strategy": "multi_pass" },
  };
  return [newCfg, decision];
};

// ---------------------------------------------------------------------------
// Rule 5 — low_transitivity
// ---------------------------------------------------------------------------

export const ruleLowTransitivity: Rule = (ctx) => {
  const { profile, config } = ctx;
  const cp = profile.cluster;
  if (cp.transitivityRate >= 0.85 || cp.nClusters === 0) return null;
  const mk = firstWeightedMatchkey(config);
  if (!mk) return null;
  const newThreshold = Math.max(THRESHOLD_FLOOR, mk.threshold - 0.05);
  if (newThreshold === mk.threshold) return null;
  const newMk: WeightedMatchkey = { ...mk, threshold: newThreshold };
  const newCfg = replaceMatchkey(config, mk, newMk);
  const decision: PolicyDecision = {
    ruleName: "low_transitivity",
    rationale: `transitivity=${cp.transitivityRate.toFixed(2)} < 0.85; lowering threshold ${mk.threshold.toFixed(2)} → ${newThreshold.toFixed(2)}`,
    configDiff: { "matchkeys[0].threshold": newThreshold },
  };
  return [newCfg, decision];
};

// ---------------------------------------------------------------------------
// Rule 6 — no_matches
// ---------------------------------------------------------------------------

export const ruleNoMatches: Rule = (ctx) => {
  const { profile, config } = ctx;
  const sp = profile.scoring;
  // candidates_compared == 0 is singleton-trap territory; don't fire here.
  if (sp.candidatesCompared === 0) return null;
  if (sp.massAboveThreshold > 0) return null;

  const mk = firstWeightedMatchkey(config);
  if (!mk) return null;
  const newThreshold = +(mk.threshold - 0.05).toFixed(2);
  if (newThreshold < THRESHOLD_FLOOR) return null;
  const newMk: WeightedMatchkey = { ...mk, threshold: newThreshold };
  const newCfg = replaceMatchkey(config, mk, newMk);
  const decision: PolicyDecision = {
    ruleName: "no_matches",
    rationale: `lowered threshold to ${newThreshold}`,
    configDiff: {},
  };
  return [newCfg, decision];
};

// ---------------------------------------------------------------------------
// Rule 7 — unimodal_scoring
// ---------------------------------------------------------------------------

export const ruleUnimodalScoring: Rule = (ctx) => {
  const { profile, config } = ctx;
  const sp = profile.scoring;
  if (sp.dipStatistic >= 0.01 || sp.nPairsScored === 0) return null;
  const mk = firstWeightedMatchkey(config);
  if (!mk) return null;
  const fieldNames = new Set(mk.fields.map((f) => f.field));
  // Pick highest-cardinality matchkey field in the per-field profile.
  const sorted: Array<[string, number]> = [];
  for (const [name, fs] of Object.entries(profile.matchkey.perField)) {
    if (fieldNames.has(name)) {
      sorted.push([name, fs.postTransformCardinalityRatio]);
    }
  }
  if (sorted.length === 0) return null;
  sorted.sort((a, b) => b[1] - a[1]);
  const target = sorted[0]![0];
  let changed = false;
  const newFields: MatchkeyField[] = [];
  for (const f of mk.fields) {
    if (f.field === target && f.scorer !== "ensemble") {
      newFields.push({ ...f, scorer: "ensemble" });
      changed = true;
    } else {
      newFields.push(f);
    }
  }
  if (!changed) return null;
  const newMk: WeightedMatchkey = { ...mk, fields: newFields };
  const newCfg = replaceMatchkey(config, mk, newMk);
  const decision: PolicyDecision = {
    ruleName: "unimodal_scoring",
    rationale: `dip_statistic=${sp.dipStatistic.toFixed(4)} < 0.01; swapping scorer on '${target}' to ensemble`,
    configDiff: { [`matchkeys[].fields[${target}].scorer`]: "ensemble" },
  };
  return [newCfg, decision];
};

// ---------------------------------------------------------------------------
// Wave 2 — indicator-aware rules
// ---------------------------------------------------------------------------

/** Rule 8 — uniform_heavy_blocking (v1.10).
 *
 * Fires when blocking creates many candidates but scoring can't separate
 * matches from non-matches — over-coarse blocking on a low-discriminating
 * key. Distinct from `blocking_too_coarse` which fires on p99 skew.
 */
export const ruleUniformHeavyBlocking: Rule = (ctx) => {
  const { profile, config } = ctx;
  const bp = profile.blocking;
  const sp = profile.scoring;
  const dp = profile.data;
  const nRows = dp.nRows;
  if (bp.nBlocks === 0 || nRows === 0) return null;
  const avgBlock = nRows / bp.nBlocks;
  if (avgBlock < 30) return null;
  if (sp.candidatesCompared < nRows) return null;
  if (sp.massAboveThreshold < 0.5) return null;
  if (sp.massInBorderline < 0.5) return null;
  if (!config.blocking) return null;

  const used = existingBlockingFields(config);
  const priorityMap: Record<string, number> = {
    email: 0,
    name: 1,
    text: 2,
    "id-like": 3,
  };
  const candidates: Array<[number, number, string, string]> = [];
  for (const [col, ratio] of Object.entries(dp.cardinalityRatio)) {
    if (used.has(col)) continue;
    if (!(ratio >= 0.3 && ratio <= 0.95)) continue;
    const colType = dp.columnTypes[col] ?? "unknown";
    if (colType in priorityMap) {
      candidates.push([priorityMap[colType]!, -ratio, col, colType]);
    }
  }
  if (candidates.length === 0) return null;
  candidates.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const [, , newField, newType] = candidates[0]!;

  const newBlocking = {
    ...config.blocking,
    strategy: "static" as const,
    keys: [
      { fields: [newField], transforms: ["lowercase", "strip"] } satisfies BlockingKeyConfig,
    ],
    passes: [],
  };
  const newCfg: GoldenMatchConfig = { ...config, blocking: newBlocking };
  const decision: PolicyDecision = {
    ruleName: "uniform_heavy_blocking",
    rationale: `avg_block_size=${avgBlock.toFixed(1)} (uniform-heavy) with mass_above=${sp.massAboveThreshold.toFixed(2)} and mass_borderline=${sp.massInBorderline.toFixed(2)}; switching to '${newField}' (${newType}, cardinality ${(dp.cardinalityRatio[newField] ?? 0).toFixed(2)})`,
    configDiff: {
      "blocking.strategy": "static",
      "blocking.keys[0].fields": [newField],
    },
  };
  return [newCfg, decision];
};

/** Rule 9 — blocking_field_null_heavy (v1.10).
 *
 * Fires when blocking on a single field whose null_rate > 0.10. Records with
 * null blocking values can't appear in any block; converts to multi-pass on
 * a low-null alternate.
 */
export const ruleBlockingFieldNullHeavy: Rule = (ctx) => {
  const { profile, config } = ctx;
  if (!config.blocking || !config.blocking.keys || config.blocking.keys.length === 0) {
    return null;
  }
  if (
    config.blocking.strategy === "multi_pass" &&
    (config.blocking.passes?.length ?? 0) > 1
  ) {
    return null;
  }
  if (config.blocking.keys.length > 1) return null;
  const primary = config.blocking.keys[0]!;
  if (!primary.fields || primary.fields.length === 0) return null;
  const primaryField = primary.fields[0]!;
  const blockingNull = profile.data.nullRate[primaryField] ?? 0.0;
  if (blockingNull <= 0.1) return null;

  const used = existingBlockingFields(config);
  const candidates: Array<[string, number]> = [];
  for (const [col, ratio] of Object.entries(profile.data.cardinalityRatio)) {
    if (used.has(col)) continue;
    const nrate = profile.data.nullRate[col] ?? 1.0;
    if (nrate >= 0.05) continue;
    if (!(ratio >= 0.05 && ratio <= 0.95)) continue;
    candidates.push([col, ratio]);
  }
  candidates.sort((a, b) => b[1] - a[1]);
  if (candidates.length === 0) return null;
  const secondField = candidates[0]![0];

  const existingKeys = Array.from(config.blocking.keys);
  const newPass: BlockingKeyConfig = {
    fields: [secondField],
    transforms: ["lowercase"],
  };
  const newBlocking = {
    ...config.blocking,
    strategy: "multi_pass" as const,
    passes: [...existingKeys, newPass],
  };
  const newCfg: GoldenMatchConfig = { ...config, blocking: newBlocking };
  const decision: PolicyDecision = {
    ruleName: "blocking_field_null_heavy",
    rationale: `blocking field '${primaryField}' has null_rate=${blockingNull.toFixed(2)} > 0.10; adding multi-pass on low-null alternate '${secondField}'`,
    configDiff: {
      "blocking.strategy": "multi_pass",
      "blocking.passes[+]": [secondField],
    },
  };
  return [newCfg, decision];
};

/** Rule 10 — recall_gap_suspected (v1.10).
 *
 * Fires when either the random-pair probe shows non-trivial off-block
 * matches, or blocking is over-tight (perfect precision + few candidates).
 */
export const ruleRecallGapSuspected: Rule = (ctx) => {
  const { profile, config } = ctx;
  const sp = profile.scoring;
  if (!config.blocking) return null;
  if (
    config.blocking.strategy === "multi_pass" &&
    (config.blocking.passes?.length ?? 0) > 1
  ) {
    return null;
  }
  const probeFires =
    sp.randomPairAboveThresholdRate !== undefined &&
    sp.randomPairAboveThresholdRate >= 0.05;
  const nRows = profile.data.nRows;
  const tightFires =
    sp.massAboveThreshold >= 1.0 &&
    sp.candidatesCompared > 0 &&
    nRows > 0 &&
    sp.candidatesCompared < nRows * 0.5 &&
    profile.blocking.reductionRatio > 0.995;
  if (!probeFires && !tightFires) return null;

  const used = existingBlockingFields(config);
  const nameTypeCols: string[] = [];
  const allCandidates: Array<[string, number]> = [];
  for (const [col, ratio] of Object.entries(profile.data.cardinalityRatio)) {
    if (used.has(col)) continue;
    const nrate = profile.data.nullRate[col] ?? 1.0;
    if (nrate >= 0.2) continue;
    if (!(ratio >= 0.05 && ratio <= 0.95)) continue;
    allCandidates.push([col, ratio]);
    const colType = profile.data.columnTypes[col] ?? "unknown";
    if (colType === "name" || colType === "text") nameTypeCols.push(col);
  }
  allCandidates.sort((a, b) => b[1] - a[1]);
  if (allCandidates.length === 0) return null;

  let secondField: string;
  let transforms: string[];
  if (tightFires && nameTypeCols.length > 0) {
    nameTypeCols.sort(
      (a, b) =>
        (profile.data.cardinalityRatio[b] ?? 0) -
        (profile.data.cardinalityRatio[a] ?? 0),
    );
    secondField = nameTypeCols[0]!;
    transforms = ["soundex"];
  } else {
    secondField = allCandidates[0]![0];
    transforms = ["lowercase"];
  }

  const existingKeys = Array.from(config.blocking.keys);
  const newPass: BlockingKeyConfig = { fields: [secondField], transforms };
  const newBlocking = {
    ...config.blocking,
    strategy: "multi_pass" as const,
    passes: [...existingKeys, newPass],
  };
  const newCfg: GoldenMatchConfig = { ...config, blocking: newBlocking };
  const rationale = probeFires
    ? `random_pair_above_threshold_rate=${(sp.randomPairAboveThresholdRate ?? 0).toFixed(3)} > 0.05; adding multi-pass on '${secondField}'`
    : `mass_above_threshold=${sp.massAboveThreshold.toFixed(3)}==1.0 with candidates_compared=${sp.candidatesCompared} < n_rows*0.5; blocking too tight; adding multi-pass on '${secondField}'`;
  const decision: PolicyDecision = {
    ruleName: "recall_gap_suspected",
    rationale,
    configDiff: {
      "blocking.strategy": "multi_pass",
      "blocking.passes[+]": [secondField],
    },
  };
  return [newCfg, decision];
};

/** Rule 11 — collision_signal_too_high (v1.10/v1.11).
 *
 * When an exact matchkey's identity column has high collision rate (same
 * value used for distinct entities by witness check), demote it to a fuzzy
 * weighted-matchkey field + add to blocking. Threshold 0.75 matches Python.
 */
export const ruleCollisionSignalTooHigh: Rule = (ctx) => {
  const { profile, config, indicators } = ctx;
  if (indicators === null || indicators === undefined) return null;
  const exactMks: { mk: MatchkeyConfig; field: string }[] = [];
  for (const mk of getMatchkeys(config)) {
    if (mk.type === "exact") {
      for (const f of mk.fields) exactMks.push({ mk, field: f.field });
    }
  }
  if (exactMks.length === 0) return null;
  const priors = indicators.columnPriors;

  for (const { mk, field } of exactMks) {
    const cp = priors[field];
    if (cp === undefined) continue;
    if (cp.identityScore < 0.85) continue;
    const ratio = profile.data.cardinalityRatio[field] ?? 0.0;
    if (!(ratio >= 0.5 && ratio <= 0.95)) continue;
    // Witnesses: other identity-score-high columns not equal to `field`.
    const witnesses = Object.entries(priors)
      .filter(([col, p]) => col !== field && p.identityScore >= 0.5)
      .map(([col]) => col);
    if (witnesses.length === 0) continue;
    const sig = indicators.collisionSignal(field, witnesses);
    if (sig.rate < COLLISION_RATE_THRESHOLD) continue;

    // Demote: drop the exact matchkey, add as fuzzy participant in first
    // weighted matchkey, and add field to blocking.
    const weightedIdx = getMatchkeys(config).findIndex((m) => m.type === "weighted");
    if (weightedIdx < 0) return null;
    const target = getMatchkeys(config)[weightedIdx] as WeightedMatchkey;
    const newField: MatchkeyField = {
      field,
      transforms: ["lowercase", "strip"],
      scorer: "token_sort",
      weight: 0.3,
    };
    const alreadyParticipating = target.fields.some((f) => f.field === field);
    const newTarget: WeightedMatchkey = alreadyParticipating
      ? target
      : { ...target, fields: [...target.fields, newField] };

    const newMatchkeys: MatchkeyConfig[] = [];
    for (const m of getMatchkeys(config)) {
      if (m === mk) continue;
      newMatchkeys.push(m === target ? newTarget : m);
    }

    let newBlocking = config.blocking;
    if (newBlocking) {
      const blockingCols = new Set<string>();
      for (const k of newBlocking.keys) for (const f of k.fields) blockingCols.add(f);
      if (!blockingCols.has(field)) {
        const newBlockKey: BlockingKeyConfig = {
          fields: [field],
          transforms: ["lowercase", "strip"],
        };
        const newKeys = [...newBlocking.keys, newBlockKey];
        const existingPasses =
          newBlocking.passes && newBlocking.passes.length > 0
            ? newBlocking.passes
            : newBlocking.keys;
        const newPasses = [...existingPasses, newBlockKey];
        newBlocking = {
          ...newBlocking,
          strategy: newKeys.length > 1 ? "multi_pass" : newBlocking.strategy,
          keys: newKeys,
          ...(newKeys.length > 1 ? { passes: newPasses } : {}),
        };
      }
    }

    const newCfg: GoldenMatchConfig = {
      ...config,
      matchkeys: newMatchkeys,
      ...(newBlocking ? { blocking: newBlocking } : {}),
    };
    const decision: PolicyDecision = {
      ruleName: "demote_clustered_identity",
      rationale: `collision_rate=${sig.rate.toFixed(2)} >= ${COLLISION_RATE_THRESHOLD} on '${field}' (witness='${sig.witnessUsed}'); demoting exact matchkey to fuzzy + adding to blocking`,
      configDiff: {
        "matchkeys.demoted": field,
        "blocking.keys[+]": [field],
      },
    };
    return [newCfg, decision];
  }
  return null;
};

/** Rule 12 — sparse_match_expand (v1.10).
 *
 * Side-channel one-shot: lower threshold by 0.10 as proxy for "expand sample".
 */
export const ruleSparseMatchExpand: Rule = (ctx) => {
  const { config, indicators, history } = ctx;
  if (indicators === null || indicators === undefined) return null;
  if (!indicators.sparsityVerdict.isSparse) return null;
  if (history.entries.length > 1) return null;
  if (indicators.hasFired("rule_sparse_match_expand")) return null;
  const mk = firstWeightedMatchkey(config);
  if (!mk) return null;
  const newThreshold = +(mk.threshold - 0.1).toFixed(2);
  if (newThreshold < THRESHOLD_FLOOR) return null;
  const newMk: WeightedMatchkey = { ...mk, threshold: newThreshold };
  const newCfg = replaceMatchkey(config, mk, newMk);
  indicators.markFired("rule_sparse_match_expand");
  const decision: PolicyDecision = {
    ruleName: "sparse_match_expand",
    rationale: `sparse_sample (n_true_pairs=${indicators.sparsityVerdict.estimatedNTruePairs}); lowered threshold to ${newThreshold}`,
    configDiff: {},
  };
  return [newCfg, decision];
};

/** Rule 13 — cross_blocking_disagreement (v1.10).
 *
 * When profile is RED, mass_above < 0.1, iter >= 1, and the cross-blocking
 * overlap between the current key and an orthogonal candidate is < 0.3,
 * propose a multi-pass.
 */
export const ruleCrossBlockingDisagreement: Rule = (ctx) => {
  const { profile, config, history, indicators } = ctx;
  if (indicators === null || indicators === undefined) return null;
  if (history.entries.length < 1) return null;
  if (complexityHealth(profile) !== HealthVerdict.RED) return null;
  if (profile.scoring.massAboveThreshold >= 0.1) return null;
  if (!config.blocking || config.blocking.keys.length === 0) return null;
  const primary = config.blocking.keys[0]!;
  if (!primary.fields || primary.fields.length === 0) return null;
  const blockingCol = primary.fields[0]!;
  const used = new Set<string>();
  for (const k of config.blocking.keys) for (const f of k.fields) used.add(f);
  const orthoCol = indicators.columns.find(
    (c) => !used.has(c) && !c.startsWith("__"),
  );
  if (orthoCol === undefined) return null;
  const overlap = indicators.crossBlockingOverlap(blockingCol, orthoCol);
  if (overlap === null || overlap >= 0.3) return null;

  const ortho: BlockingKeyConfig = {
    fields: [orthoCol],
    transforms: ["lowercase"],
  };
  // Don't re-add identical fields.
  if (config.blocking.keys.some((k) => k.fields.join("|") === ortho.fields.join("|"))) {
    return null;
  }
  const newKeys = [...config.blocking.keys, ortho];
  const newBlocking = {
    ...config.blocking,
    strategy: "multi_pass" as const,
    keys: newKeys,
    passes: newKeys,
  };
  const newCfg: GoldenMatchConfig = { ...config, blocking: newBlocking };
  const decision: PolicyDecision = {
    ruleName: "cross_blocking_disagreement",
    rationale: `cross_blocking_overlap=${overlap.toFixed(2)} < 0.3; adding multi_pass on '${orthoCol}'`,
    configDiff: {},
  };
  return [newCfg, decision];
};

/** Rule 14 — corruption_normalize (v1.10).
 *
 * When the blocking column has both high corruption (> 0.4) and high
 * identity prior (> 0.6) and the profile is non-GREEN, mark the config as
 * needing normalize-standardization on that column. Without a full
 * StandardizationConfig in the TS surface we mirror the Python signal by
 * tagging the matchkey field's transforms — appending `lowercase`+`strip`
 * if missing. Lower-bound port: if no eligible field exists, return null.
 */
export const ruleCorruptionNormalize: Rule = (ctx) => {
  const { profile, config, indicators } = ctx;
  if (indicators === null || indicators === undefined) return null;
  if (complexityHealth(profile) === HealthVerdict.GREEN) return null;
  if (!config.blocking || config.blocking.keys.length === 0) return null;
  const blockingCol = config.blocking.keys[0]!.fields[0];
  if (blockingCol === undefined) return null;
  const cp = indicators.columnPriors[blockingCol];
  if (cp === undefined) return null;
  if (cp.corruptionScore <= 0.4 || cp.identityScore <= 0.6) return null;

  const existing = config.blocking.keys[0]!.transforms ?? [];
  const wanted = ["lowercase", "strip"];
  const additions = wanted.filter((t) => !existing.includes(t));
  if (additions.length === 0) return null;
  const newKey: BlockingKeyConfig = {
    fields: config.blocking.keys[0]!.fields,
    transforms: [...existing, ...additions],
  };
  const newKeys = [newKey, ...config.blocking.keys.slice(1)];
  const newBlocking = { ...config.blocking, keys: newKeys };
  const newCfg: GoldenMatchConfig = { ...config, blocking: newBlocking };
  const decision: PolicyDecision = {
    ruleName: "corruption_normalize",
    rationale: `corruption=${cp.corruptionScore.toFixed(2)} identity=${cp.identityScore.toFixed(2)}; appended normalize transforms on '${blockingCol}'`,
    configDiff: { "blocking.keys[0].transforms": newKey.transforms },
  };
  return [newCfg, decision];
};

// ---------------------------------------------------------------------------
// DEFAULT_RULES_V1_7_V1_8 — order matches Python's DEFAULT_RULES (the
// subset that does not require an IndicatorContext).
// ---------------------------------------------------------------------------

export const DEFAULT_RULES_V1_7_V1_8: readonly Rule[] = [
  ruleBlockingSingletonTrap,
  ruleBlockingKeySwap,
  ruleBlockingTooCoarse,
  ruleUnimodalScoring,
  ruleLowReductionRatio,
  ruleLowTransitivity,
  ruleNoMatches,
];

/**
 * v1.10 default rule list — mirrors Python ``DEFAULT_RULES`` order. Includes
 * the 7 base rules plus 4 indicator-aware rules. Rules that consume
 * ``ctx.indicators`` no-op when the controller didn't provision one
 * (back-compat with v1.7/v1.8 callers).
 */
export const DEFAULT_RULES_V1_10: readonly Rule[] = [
  ruleBlockingFieldNullHeavy,           // 1  structural
  ruleBlockingSingletonTrap,            // 2  structural
  ruleBlockingKeySwap,                  // 3  structural
  ruleBlockingTooCoarse,                // 4  structural
  ruleUniformHeavyBlocking,             // 5  structural
  ruleCorruptionNormalize,              // 6  v1.10 (indicators)
  ruleCollisionSignalTooHigh,           // 7  v1.11 (indicators)
  ruleUnimodalScoring,                  // 8  tuning
  ruleLowReductionRatio,                // 9  structural
  ruleCrossBlockingDisagreement,        // 10 v1.10 (indicators)
  ruleLowTransitivity,                  // 11 tuning
  ruleNoMatches,                        // 12 tuning
  ruleRecallGapSuspected,               // 13 tuning
  ruleSparseMatchExpand,                // 14 v1.10 (indicators)
];
