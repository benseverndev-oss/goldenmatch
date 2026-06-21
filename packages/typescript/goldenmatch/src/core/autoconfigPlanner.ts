/**
 * autoconfigPlanner.ts — planner-rule dispatcher for controller v3.
 * Edge-safe: no `node:` imports.
 *
 * Ports goldenmatch/core/autoconfig_planner.py. By DEFAULT the pure-TS rules
 * are evaluated in registry order (first matching predicate fires). When the
 * opt-in `goldenmatch/core/autoconfig-wasm` backend has been enabled, planning
 * instead routes through the shared `goldenmatch-autoconfig-core` wasm — the
 * SAME core the Python `goldenmatch-native` wheel calls — for byte-parity across
 * Python / Rust / TS (the TS rules are a faithful port, so the two agree; the
 * parity test guards it). This mirrors Python's default-OFF native gate.
 */

import type { ComplexityProfile } from "./complexityProfile.js";
import type { RuntimeProfile } from "./runtimeProfile.js";
import type { BackendName, ExecutionPlan } from "./executionPlan.js";
import { makeExecutionPlan } from "./executionPlan.js";
import { getAutoconfigWasmBackend } from "./autoconfigWasmBackend.js";

export interface PlannerContext {
  readonly userBackend?: string | null;
}

const KNOWN_BACKENDS: ReadonlySet<string> = new Set<BackendName>([
  "polars-direct",
  "bucket",
  "chunked",
  "duckdb",
  "ray",
]);

/**
 * Normalize a user backend override into the wasm core's `user_backend`. Empty/
 * absent → null (auto); an unrecognized backend is also coerced to null, since
 * the core only accepts the known BackendName enum (passing garbage would throw
 * on deserialize) — a bad override falls through to auto-planning, never crashes.
 */
function normalizeUserBackend(
  userBackend: string | null | undefined,
): BackendName | null {
  if (userBackend === undefined || userBackend === null || userBackend === "") {
    return null;
  }
  return KNOWN_BACKENDS.has(userBackend) ? (userBackend as BackendName) : null;
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
  // Opt-in fast path: route through the shared wasm decision core when enabled.
  const wasm = getAutoconfigWasmBackend();
  if (wasm !== null) {
    return wasm.decidePlan({
      nRowsFull,
      estimatedPairCount: profile.blocking.totalComparisons,
      runtime: {
        availableRamGb: runtime.availableRamGb,
        cpuCount: runtime.cpuCount,
        diskFreeGb: runtime.diskFreeGb,
      },
      caps: {
        // The edge-safe TS core never has a native bucket kernel or ray binding.
        bucketAvailable: false,
        rayAvailable: false,
        rayAutoSelect: false,
        userBackend: normalizeUserBackend(context.userBackend),
      },
    });
  }

  // Default: the pure-TS rule table (faithful port of the Python planner).
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
