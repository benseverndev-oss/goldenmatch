/**
 * scheduler.ts -- scheduled dedupe runs. Ports `goldenmatch/core/scheduler.py`.
 *
 * Node-only: it writes output files and sleeps between runs.
 *
 * PARITY WARNING (deliberate, do not "fix" one-sided): `parseCron` is a FAITHFUL
 * port of Python's deliberately-simplified cron handling. It validates the 5-field
 * shape and then collapses to a coarse interval (daily / hourly) -- it does NOT
 * compute the next matching wall-clock time. Python's own docstring says "For full
 * cron support, use system cron."
 *
 * Implementing real cron here would make `--cron "0 6 * * *"` mean *daily at 06:00*
 * on TS and *every 86400s starting now* on Python -- a silent operational
 * divergence in a scheduling tool, which is worse than a shared limitation. If this
 * should become real cron, it needs to change on BOTH surfaces in one go.
 */
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { dedupe } from "../core/api.js";
import { writeCsv } from "./connectors/file.js";
import type { GoldenMatchConfig, Row } from "../core/types.js";

/** Python `parse_interval`: '30s' | '30m' | '1h' | '1d' | bare seconds. */
export function parseInterval(spec: string): number {
  const s = spec.trim().toLowerCase();
  const suffixes: Array<[string, number]> = [
    ["s", 1],
    ["m", 60],
    ["h", 3600],
    ["d", 86400],
  ];
  // The numeric part must be a whole number. Python does `int(spec[:-1])`, which
  // RAISES on "1.5h" -- so accepting it here (parseInt would silently yield 1)
  // would mean a scheduler that runs hourly on TS and errors on Python for the
  // same flag. Reject it on both.
  for (const [suffix, mult] of suffixes) {
    if (s.endsWith(suffix)) {
      const body = s.slice(0, -1);
      if (!/^-?\d+$/.test(body)) {
        throw new Error(`Invalid interval: ${spec}. Use format: 30m, 1h, 6h, 1d`);
      }
      return Number.parseInt(body, 10) * mult;
    }
  }
  if (!/^-?\d+$/.test(s)) {
    throw new Error(`Invalid interval: ${spec}. Use format: 30m, 1h, 6h, 1d`);
  }
  return Number.parseInt(s, 10);
}

/**
 * Python `parse_cron`: validate 5 fields, then return a COARSE interval.
 * See the module-level parity warning -- this is intentionally not real cron.
 */
export function parseCron(cronSpec: string): number {
  const parts = cronSpec.trim().split(/\s+/).filter((p) => p !== "");
  if (parts.length !== 5) {
    throw new Error("Cron spec must have 5 fields: minute hour day month weekday");
  }
  const [minute, hour] = parts as [string, string, string, string, string];
  if (hour !== "*" && minute !== "*") return 86400; // daily
  if (minute !== "*") return 3600; // hourly
  return 3600; // default hourly
}

export interface ScheduledRunResult {
  readonly runNumber: number;
  readonly totalRecords: number;
  readonly totalClusters: number;
  readonly matchRate: number;
  readonly outputPath: string;
}

export interface ScheduledJobOptions {
  readonly jobId: string;
  readonly filePaths: readonly string[];
  readonly config?: GoldenMatchConfig;
  readonly intervalSeconds?: number;
  readonly outputDir?: string;
  readonly loadRows: (paths: readonly string[]) => Row[];
  readonly onComplete?: (r: ScheduledRunResult) => void;
  readonly out?: (s: string) => void;
}

/**
 * A repeating dedupe job. `runOnce` is separated from `run` so a single execution
 * is testable without any timers -- Python's loop and its body are entangled.
 */
export class ScheduledJob {
  readonly jobId: string;
  readonly intervalSeconds: number;
  runCount = 0;
  lastRun: Date | null = null;
  lastResult: ScheduledRunResult | null = null;

  private readonly opts: ScheduledJobOptions;
  private stopped = false;

  constructor(opts: ScheduledJobOptions) {
    this.opts = opts;
    this.jobId = opts.jobId;
    this.intervalSeconds = opts.intervalSeconds ?? 3600;
  }

  async runOnce(): Promise<ScheduledRunResult> {
    const rows = this.opts.loadRows(this.opts.filePaths);
    const result = await dedupe(rows, this.opts.config ? { config: this.opts.config } : {});
    this.runCount++;
    this.lastRun = new Date();

    const dir = this.opts.outputDir ?? ".";
    mkdirSync(dir, { recursive: true });
    // Run number in the filename so successive runs don't clobber each other.
    const outputPath = join(dir, `${this.jobId}_run${this.runCount}_golden.csv`);
    writeCsv(outputPath, result.goldenRecords as readonly Row[]);

    const summary: ScheduledRunResult = {
      runNumber: this.runCount,
      totalRecords: result.stats.totalRecords,
      totalClusters: result.stats.totalClusters,
      matchRate: result.stats.matchRate,
      outputPath,
    };
    this.lastResult = summary;
    this.opts.onComplete?.(summary);
    return summary;
  }

  stop(): void {
    this.stopped = true;
  }

  /**
   * Run forever (until `stop()`), sleeping `intervalSeconds` between runs.
   *
   * `sleep` and `maxRuns` are injectable so the loop itself is testable -- a real
   * timer-driven loop is otherwise untestable, which is why Python's has no test.
   * An error in one run is reported and the schedule CONTINUES; a scheduled job
   * that dies on one bad input is worse than one that logs and retries.
   */
  async run(
    opts: { maxRuns?: number; sleep?: (ms: number) => Promise<void> } = {},
  ): Promise<void> {
    const out = this.opts.out ?? (() => {});
    const sleep =
      opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
    const maxRuns = opts.maxRuns ?? Infinity;

    out(`Scheduled job ${this.jobId}: every ${this.intervalSeconds}s`);
    // maxRuns bounds ATTEMPTS, not successes. Counting only successful runs means
    // a permanently-failing job ignores `--max-runs` and loops forever, since a
    // failed attempt never advances runCount.
    let attempts = 0;
    while (!this.stopped && attempts < maxRuns) {
      attempts++;
      const started = Date.now();
      try {
        const r = await this.runOnce();
        out(
          `  run ${r.runNumber}: ${r.totalRecords} records -> ${r.totalClusters} clusters ` +
            `(${(r.matchRate * 100).toFixed(1)}%) in ${((Date.now() - started) / 1000).toFixed(1)}s`,
        );
      } catch (err) {
        out(`  run failed: ${(err as Error).message}`);
      }
      if (this.stopped || attempts >= maxRuns) break;
      await sleep(this.intervalSeconds * 1000);
    }
    out(
      `Job ${this.jobId} stopped after ${attempts} attempt(s), ${this.runCount} successful run(s).`,
    );
  }
}
