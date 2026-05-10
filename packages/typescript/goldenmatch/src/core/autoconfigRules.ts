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

const THRESHOLD_FLOOR = 0.5;

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
