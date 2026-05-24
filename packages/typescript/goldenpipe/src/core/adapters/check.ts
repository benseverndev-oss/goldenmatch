/**
 * GoldenCheck adapter — wraps GoldenCheck-JS `scanData`.
 * Port of goldenpipe/adapters/check.py.
 *
 * Shape divergence vs Python: the Python adapter calls `scan_file(path)` and
 * reads `ctx.metadata["source"]`. GoldenCheck-JS's edge-safe `scanData` instead
 * operates on a `TabularData` built from rows, so the TS adapter scans
 * `ctx.df` directly. This means `goldencheck.scan` succeeds in the in-memory
 * (`runDf`) path here, whereas the Python `run_df` path fails the scan stage
 * (it has no file). Use `run(source)` for cross-language parity.
 *
 * Edge-safe: no `node:` imports (GoldenCheck-JS core is edge-safe).
 */

import { scanData, TabularData, severityLabel } from "goldencheck/core";
import type { Finding, ColumnProfile } from "goldencheck/core";
import type { PipeContext, Stage, StageResult } from "../models.js";
import { StageStatus } from "../models.js";
import {
  buildContextsFromCheck,
  type ColumnProfileLike,
  type FindingLike,
} from "../columnContext.js";

interface NormalizedFinding {
  severity: string;
  check: string;
  column: string;
  message: string;
}

/** Normalize a GoldenCheck-JS Finding (numeric severity) to the dict shape
 *  the Python pipeline used (string severity label). */
function normalizeFinding(f: Finding): NormalizedFinding {
  return {
    severity: severityLabel(f.severity).toLowerCase(),
    check: f.check,
    column: f.column,
    message: f.message,
  };
}

/** Map a GoldenCheck-JS ColumnProfile to the minimal shape columnContext consumes. */
function toColumnProfileLike(cp: ColumnProfile): ColumnProfileLike {
  return {
    name: cp.name,
    inferredType: cp.inferredType,
    nullPct: cp.nullPct,
    uniqueCount: cp.uniqueCount,
  };
}

export const ScanStage: Stage = {
  info: { name: "goldencheck.scan", produces: ["findings", "profile"], consumes: ["df"] },

  validate(ctx: PipeContext): void {
    if (ctx.df === null) {
      throw new Error("ScanStage: no df in context");
    }
  },

  async run(ctx: PipeContext): Promise<StageResult> {
    const rows = ctx.df ?? [];
    const data = new TabularData(rows);
    const stageCfg = ctx.stageConfig;
    const opts = stageCfg && Object.keys(stageCfg).length > 0 ? stageCfg : undefined;
    const result = scanData(data, opts as Parameters<typeof scanData>[1]);

    const findings: NormalizedFinding[] = result.findings.map(normalizeFinding);
    const columnProfiles = result.profile.columns;

    ctx.artifacts["findings"] = findings;
    ctx.artifacts["profile"] = result.profile;

    // Build column contexts for downstream stages (best-effort enrichment).
    try {
      const profileLikes: ColumnProfileLike[] = columnProfiles.map(toColumnProfileLike);
      const findingLikes: FindingLike[] = findings.map((f) => ({
        column: f.column,
        check: f.check,
        message: f.message,
      }));
      ctx.artifacts["column_contexts"] = buildContextsFromCheck(findingLikes, profileLikes);
    } catch {
      ctx.artifacts["column_contexts"] = [];
    }

    return { status: StageStatus.SUCCESS };
  },

  rollback: null,
};
