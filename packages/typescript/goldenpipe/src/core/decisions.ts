/**
 * Built-in decision functions for pipeline routing.
 * Port of goldenpipe/decisions.py.
 *
 * Edge-safe: no `node:` imports.
 *
 * NOTE on TS sibling skew: GoldenCheck-JS `Finding.severity` is a numeric enum
 * (INFO=1, WARNING=2, ERROR=3) and has no `"critical"` level, and there is no
 * `"pii_detection"` check. The check adapter normalizes findings to a string
 * `severity` label ("info"/"warning"/"error") and a `check` string. So in
 * practice `severityGate` and `piiRouter` are no-ops against current
 * GoldenCheck-JS output — they are ported for structural parity and so that
 * custom stages emitting `"critical"` / `"pii_detection"` findings still route.
 */

import type { Decision, PipeContext } from "./models.js";
import { makeDecision } from "./models.js";
import { getPipeWasmBackend } from "./wasm/backend.js";
import { evaluateBuiltinViaWasm } from "./wasm/plannerJson.js";

interface NormalizedFinding {
  severity?: unknown;
  check?: unknown;
}

function findingsOf(ctx: PipeContext): NormalizedFinding[] | null {
  const findings = ctx.artifacts["findings"];
  if (!Array.isArray(findings) || findings.length === 0) return null;
  return findings as NormalizedFinding[];
}

// ---------------------------------------------------------------------------
// Guarded-public / pure-core split. The exported gate names (severityGate /
// piiRouter / rowCountGate) stay stable for external consumers and route
// through the WASM planner backend when one is registered; the `*Pure` cores
// hold the original bodies and are called guard-free by plannerJsonPure (no
// recursion).
// ---------------------------------------------------------------------------

/** Abort the pipeline if any finding has `critical` severity. */
export function severityGatePure(ctx: PipeContext): Decision | null {
  const findings = findingsOf(ctx);
  if (!findings) return null;

  const hasCritical = findings.some((f) => f.severity === "critical");
  if (hasCritical) {
    return makeDecision({ abort: true, reason: "Critical findings detected" });
  }
  return null;
}

export function severityGate(ctx: PipeContext): Decision | null {
  const b = getPipeWasmBackend();
  if (b) return evaluateBuiltinViaWasm("severity_gate", ctx, b);
  return severityGatePure(ctx);
}

/** Route to PPRL matching if PII is detected. */
export function piiRouterPure(ctx: PipeContext): Decision | null {
  const findings = findingsOf(ctx);
  if (!findings) return null;

  const hasPii = findings.some((f) => f.check === "pii_detection");
  if (hasPii) {
    return makeDecision({
      skip: ["goldenmatch.dedupe"],
      insert: ["goldenmatch.dedupe_pprl"],
      reason: "PII detected, routing to PPRL matching",
    });
  }
  return null;
}

export function piiRouter(ctx: PipeContext): Decision | null {
  const b = getPipeWasmBackend();
  if (b) return evaluateBuiltinViaWasm("pii_router", ctx, b);
  return piiRouterPure(ctx);
}

/** Skip matching if fewer than 2 rows. */
export function rowCountGatePure(ctx: PipeContext): Decision | null {
  const rowCount =
    typeof ctx.metadata["input_rows"] === "number" ? (ctx.metadata["input_rows"] as number) : 0;
  if (rowCount < 2) {
    return makeDecision({
      skip: ["goldenmatch.dedupe"],
      reason: `Only ${rowCount} row(s), skipping deduplication`,
    });
  }
  return null;
}

export function rowCountGate(ctx: PipeContext): Decision | null {
  const b = getPipeWasmBackend();
  if (b) return evaluateBuiltinViaWasm("row_count_gate", ctx, b);
  return rowCountGatePure(ctx);
}
