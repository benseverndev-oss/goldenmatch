/**
 * autoconfigPolicy.ts — RefitPolicy interface + HeuristicRefitPolicy.
 *
 * Port of Python ``goldenmatch/core/autoconfig_policy.py`` (v1.7).
 * Edge-safe: no `node:` imports.
 */

import type { GoldenMatchConfig } from "./types.js";
import {
  type ComplexityProfile,
  HealthVerdict,
  complexityHealth,
} from "./complexityProfile.js";
import type { RunHistory, PolicyDecision } from "./autoconfigHistory.js";
import type { IndicatorContext } from "./indicators.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface RuleContext {
  readonly profile: ComplexityProfile;
  readonly config: GoldenMatchConfig;
  readonly history: RunHistory;
  /** Optional v1.10 indicator memoization layer. ``null`` when the
   *  controller did not provision an IndicatorContext for this iteration
   *  (e.g. running with the legacy v1.7/v1.8 rule set). */
  readonly indicators?: IndicatorContext | null;
}

/** A rule is a pure function returning a new (config, decision) tuple or null. */
export type RuleOutcome = readonly [GoldenMatchConfig, PolicyDecision];
export type Rule = (ctx: RuleContext) => RuleOutcome | null;

export interface RefitPolicy {
  propose(
    profile: ComplexityProfile,
    current: GoldenMatchConfig,
    history: RunHistory,
    indicators?: IndicatorContext | null,
  ): GoldenMatchConfig | null;
}

// ---------------------------------------------------------------------------
// HeuristicRefitPolicy
// ---------------------------------------------------------------------------

/**
 * Ordered rule table. First rule returning non-null wins.
 *
 * Return semantics:
 *   - ``null`` → satisfied; controller breaks with POLICY_SATISFIED.
 *   - A new config that ``===`` current is also treated as satisfied (bug guard).
 *   - A previously-seen config is allowed; oscillation handled by controller.
 */
export class HeuristicRefitPolicy implements RefitPolicy {
  private readonly rules: readonly Rule[];

  /**
   * @param rules Rule list. Required to avoid an ESM circular dependency
   *   with the rules module — callers typically pass
   *   ``DEFAULT_RULES_V1_7_V1_8`` from ``autoconfigRules.js``. Use
   *   ``createDefaultPolicy()`` for the no-arg convenience that wires it up.
   */
  constructor(rules: readonly Rule[]) {
    this.rules = rules;
  }

  propose(
    profile: ComplexityProfile,
    current: GoldenMatchConfig,
    history: RunHistory,
    indicators?: IndicatorContext | null,
  ): GoldenMatchConfig | null {
    if (complexityHealth(profile) === HealthVerdict.GREEN) return null;
    const ctx: RuleContext = {
      profile,
      config: current,
      history,
      ...(indicators !== undefined ? { indicators } : {}),
    };
    for (const rule of this.rules) {
      const outcome = rule(ctx);
      if (outcome === null) continue;
      const [newConfig, decision] = outcome;
      if (configsEqual(newConfig, current)) {
        // Bug guard: rule "decided to do nothing" without saying so. Treat as satisfied.
        return null;
      }
      // Attach the decision to the latest history entry, matching Python.
      if (history.entries.length > 0) {
        history.entries[history.entries.length - 1]!.decision = decision;
      }
      return newConfig;
    }
    return null;
  }
}

function configsEqual(a: GoldenMatchConfig, b: GoldenMatchConfig): boolean {
  // Structural equality via JSON serialization. Configs are plain readonly
  // objects of plain values; this is the same trick Python ``==`` would do
  // via pydantic model comparison for our purposes.
  return JSON.stringify(a) === JSON.stringify(b);
}

/**
 * Convenience constructor wiring up the Wave-1 (Python v1.7/v1.8) rule list.
 * Use this when you want the default heuristic policy without manually
 * importing the rules module.
 */
export async function createDefaultPolicy(): Promise<HeuristicRefitPolicy> {
  const mod = await import("./autoconfigRules.js");
  return new HeuristicRefitPolicy(mod.DEFAULT_RULES_V1_7_V1_8);
}
