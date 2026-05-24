/**
 * autoconfigPlanner.ts — planner-rule dispatcher for controller v3.
 * Edge-safe: no `node:` imports.
 *
 * Ports goldenmatch/core/autoconfig_planner.py. Rules are evaluated in
 * registry order; the first predicate to return true fires and its action
 * returns an ExecutionPlan. A default plan is returned when no rule matches
 * (rule_name='no_rules_registered' for empty registry, 'no_rule_matched'
 * otherwise).
 */

import type { ComplexityProfile } from "./complexityProfile.js";
import type { RuntimeProfile } from "./runtimeProfile.js";
import type { ExecutionPlan } from "./executionPlan.js";
import { makeExecutionPlan } from "./executionPlan.js";

export interface PlannerContext {
  readonly userBackend?: string | null;
}

export type PlannerPredicate = (
  profile: ComplexityProfile,
  runtime: RuntimeProfile,
  nRowsFull: number,
  context: PlannerContext,
) => boolean;

export type PlannerAction = (
  profile: ComplexityProfile,
  runtime: RuntimeProfile,
  nRowsFull: number,
  context: PlannerContext,
) => ExecutionPlan;

export interface PlannerRule {
  readonly name: string;
  readonly predicate: PlannerPredicate;
  readonly action: PlannerAction;
}

/**
 * Walk the rule list in order; the first match's action returns the plan.
 * Mirrors Python ``apply_planner_rules``.
 */
export function applyPlannerRules(
  profile: ComplexityProfile,
  runtime: RuntimeProfile,
  nRowsFull: number,
  rules: readonly PlannerRule[],
  context: PlannerContext = {},
): ExecutionPlan {
  if (rules.length === 0) {
    return makeExecutionPlan({ ruleName: "no_rules_registered" });
  }
  for (const rule of rules) {
    if (!rule.predicate(profile, runtime, nRowsFull, context)) continue;
    const plan = rule.action(profile, runtime, nRowsFull, context);
    if (plan.ruleName === null) {
      return { ...plan, ruleName: rule.name };
    }
    return plan;
  }
  return makeExecutionPlan({ ruleName: "no_rule_matched" });
}
