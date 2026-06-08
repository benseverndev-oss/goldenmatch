/**
 * `ReportHistory` — append-only JSONL store of `AnalysisReport`s for cross-run trend
 * + regression detection (Node-only; uses `node:fs`). The pure decision logic lives
 * in `src/core` (`buildTrend` / `detectRegressions`); this wraps storage + dataset
 * filtering around it. Keyed by `(analysisName, dataset, runId)`, last-wins on read.
 *
 * JSONL only — Node 20 has no stable built-in SQLite (`node:sqlite` is experimental
 * in 22+). The SQLite backend the Python sibling offers is a documented follow-up;
 * Python's DEFAULT backend is also jsonl, so the surface parity holds. Parity with
 * `packages/python/goldenanalysis/goldenanalysis/history.py`.
 */

import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname } from "node:path";
import { buildTrend, detectRegressions } from "../core/history.js";
import type { DetectOptions } from "../core/history.js";
import type { Regression, TrendSeries } from "../core/regressions.js";
import type { AnalysisReport } from "../core/types.js";

export const SCHEMA_VERSION = 1;

/** One JSONL line: the wire `AnalysisReport` plus the storage envelope (camelCase —
 * envelope metadata is internal, unlike the snake_case `report` wire payload). */
interface HistoryRecord {
  readonly analysisName: string;
  readonly dataset: string;
  readonly runId: string;
  readonly schemaVersion: number;
  readonly recordedAt: string;
  readonly report: AnalysisReport;
}

export interface ReportHistoryOptions {
  readonly path?: string;
}

export interface QueryOptions {
  readonly analysisName?: string;
}

export class ReportHistory {
  private readonly path: string;

  constructor(options: ReportHistoryOptions = {}) {
    this.path = options.path ?? ".golden/analysis.jsonl";
    const dir = dirname(this.path);
    if (dir.length > 0 && !existsSync(dir)) mkdirSync(dir, { recursive: true });
  }

  /** Record a report. Re-appending the same `(analysisName, dataset, runId)` replaces
   * the prior one on read (idempotent upsert). */
  append(report: AnalysisReport, analysisName = "default"): void {
    const dataset = report.source["dataset"] ?? "frame";
    const record: HistoryRecord = {
      analysisName,
      dataset,
      runId: report.run_id,
      schemaVersion: report.schema_version,
      recordedAt: new Date().toISOString(),
      report,
    };
    appendFileSync(this.path, JSON.stringify(record) + "\n", "utf-8");
  }

  private allRecords(): HistoryRecord[] {
    if (!existsSync(this.path)) return [];
    const out: HistoryRecord[] = [];
    for (const line of readFileSync(this.path, "utf-8").split("\n")) {
      const trimmed = line.trim();
      if (trimmed.length === 0) continue;
      out.push(JSON.parse(trimmed) as HistoryRecord);
    }
    return out;
  }

  private static key(rec: HistoryRecord): string {
    return JSON.stringify([rec.analysisName, rec.dataset, rec.runId]);
  }

  /** Reports for `(analysisName, dataset)` in insertion order, last-wins per runId. */
  reports(dataset: string, options: QueryOptions = {}): AnalysisReport[] {
    const analysisName = options.analysisName ?? "default";
    const order = new Map<string, number>();
    const latest = new Map<string, HistoryRecord>();
    this.allRecords().forEach((rec, i) => {
      const key = ReportHistory.key(rec);
      if (!order.has(key)) order.set(key, i);
      latest.set(key, rec);
    });
    const picked: Array<readonly [number, HistoryRecord]> = [];
    for (const [key, rec] of latest) {
      if (rec.analysisName === analysisName && rec.dataset === dataset) {
        picked.push([order.get(key)!, rec] as const);
      }
    }
    picked.sort((a, b) => a[0] - b[0]);
    return picked.map(([, rec]) => rec.report);
  }

  /** A metric's value across the run history (oldest -> newest). */
  trend(metricKey: string, dataset: string, options: QueryOptions & { lastN?: number } = {}): TrendSeries {
    return buildTrend(this.reports(dataset, options), metricKey, dataset, options.lastN ?? 30);
  }

  /** Flag metric movements in the LATEST report vs the prior history. */
  detectRegressions(dataset: string, options: DetectOptions & QueryOptions = {}): Regression[] {
    return detectRegressions(this.reports(dataset, options), options);
  }
}
