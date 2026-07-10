/**
 * repairHost.ts — TS producer glue for the repair plan (mirror of
 * goldenpipe/repair_host.py). Samples column values from the row array, builds
 * ColumnInputs, calls the pure repair.ts kernel, attaches the advisory artifact.
 * Also holds the Phase-2 FIXERS allowlist + config conversion (host policy,
 * identical to repair_host.py — NOT the parity-gated kernel).
 */
import { buildRepairPlan } from "./repair.js";
import type { ColumnInput, Finding, RepairPlan } from "./repair.js";
import type { ColumnContext } from "./columnContext.js";
import type { PipeContext, Row } from "./models.js";
import type { TransformSpec } from "goldenflow/core";

const SAMPLE_LIMIT = 20;

export function sampleColumn(rows: Row[], col: string, limit = SAMPLE_LIMIT): string[] {
  const out: string[] = [];
  for (const row of rows) {
    const v = (row as Record<string, unknown>)[col];
    if (v === null || v === undefined) continue;
    const s = String(v);
    if (s.trim() === "") continue;
    out.push(s);
    if (out.length >= limit) break;
  }
  return out;
}

export function buildColumnInputs(contexts: ColumnContext[], rows: Row[]): ColumnInput[] {
  const first = rows.length > 0 ? rows[0] : undefined;
  const names = new Set<string>(first ? Object.keys(first as object) : []);
  const cols: ColumnInput[] = [];
  for (const ctx of contexts) {
    if (!names.has(ctx.name)) continue;
    cols.push({ name: ctx.name, coarse_type: ctx.inferredType, samples: sampleColumn(rows, ctx.name) });
  }
  return cols;
}

export function attachRepairPlan(
  ctx: PipeContext,
  findings: Finding[],
  contexts: ColumnContext[],
  rows: Row[],
): RepairPlan {
  const columns = buildColumnInputs(contexts, rows);
  const plan = buildRepairPlan(findings, columns);
  ctx.artifacts["repair_plan"] = plan;
  const lines = plan.repairs.map(
    (item) => `repair: ${item.column} (${item.check}) -> ${item.suggested_transforms.join(",")} [${item.reason}]`,
  );
  if (lines.length > 0) ctx.reasoning["repair_plan"] = lines.join("\n");
  return plan;
}

// ── FIXERS allowlist + conversion (identical to repair_host.py) ──────────
export const FIXERS: ReadonlySet<string> = new Set([
  "fix_mojibake", "normalize_unicode", "date_parse", "email_normalize",
  "email_canonical", "name_proper", "phone_national", "zip_normalize",
]);

export function repairTransformSpecs(plan: RepairPlan | undefined): {
  specs: TransformSpec[];
  skipped: Array<{ column: string; op: string }>;
} {
  const byCol = new Map<string, string[]>();
  const order: string[] = [];
  const skipped: Array<{ column: string; op: string }> = [];
  for (const item of plan?.repairs ?? []) {
    const col = item.column;
    for (const op of item.suggested_transforms ?? []) {
      if (FIXERS.has(op)) {
        let ops = byCol.get(col);
        if (ops === undefined) { ops = []; byCol.set(col, ops); order.push(col); }
        if (!ops.includes(op)) ops.push(op);
      } else {
        skipped.push({ column: col, op });
      }
    }
  }
  const specs: TransformSpec[] = order.map((c) => ({ column: c, ops: byCol.get(c) ?? [] }));
  return { specs, skipped };
}

export function mergeTransforms(user: readonly TransformSpec[], repair: readonly TransformSpec[]): TransformSpec[] {
  const byCol = new Map<string, string[]>();
  const order: string[] = [];
  for (const spec of [...user, ...repair]) {
    let ops = byCol.get(spec.column);
    if (ops === undefined) { ops = []; byCol.set(spec.column, ops); order.push(spec.column); }
    ops.push(...spec.ops);
  }
  return order.map((col) => {
    const seen = new Set<string>();
    const ops: string[] = [];
    for (const op of byCol.get(col) ?? []) if (!seen.has(op)) { seen.add(op); ops.push(op); }
    return { column: col, ops };
  });
}
