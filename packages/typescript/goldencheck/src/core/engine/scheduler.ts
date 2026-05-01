/**
 * Scheduled scans — run goldencheck on a repeating interval.
 * Port of goldencheck/engine/scheduler.py.
 * Node-only (uses node:fs via reader, signal handling).
 */

import type { Finding } from "../types.js";
import { Severity, healthScore } from "../types.js";
import { scanData } from "./scanner.js";
import { applyConfidenceDowngrade } from "./confidence.js";
import { recordScan, getPreviousScan } from "./history.js";
import { shouldNotify, sendWebhook } from "./notifier.js";
import { reportJson } from "../reporters/json.js";

export interface ScheduleOptions {
  files: readonly string[];
  interval: string; // "hourly", "daily", "weekly", "5min", "15min", "30min", or seconds as string
  domain?: string;
  webhook?: string;
  notifyOn?: string;
  jsonOutput?: boolean;
}

// Named interval mappings (seconds)
const INTERVALS: Record<string, number> = {
  hourly: 3600,
  daily: 86400,
  weekly: 604800,
  "5min": 300,
  "15min": 900,
  "30min": 1800,
};

function parseInterval(interval: string): number {
  if (interval in INTERVALS) {
    return INTERVALS[interval]!;
  }
  const parsed = parseInt(interval, 10);
  if (isNaN(parsed) || parsed <= 0) {
    throw new Error(
      `Invalid interval: ${interval}. Use: ${Object.keys(INTERVALS).join(", ")} or seconds.`,
    );
  }
  return parsed;
}

function timestamp(): string {
  const now = new Date();
  return `[${now.toTimeString().slice(0, 8)}]`;
}

/**
 * Sleep for the given number of seconds, checking the abort signal every second.
 * Resolves to true if sleep completed, false if aborted.
 */
function sleepWithAbort(seconds: number, signal: AbortSignal): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    let elapsed = 0;
    const tick = () => {
      if (signal.aborted || elapsed >= seconds) {
        resolve(!signal.aborted);
        return;
      }
      elapsed += 1;
      setTimeout(tick, 1000);
    };
    tick();
  });
}

/**
 * Run scheduled scans at a fixed interval.
 * Scans each file, records history, prints results, fires webhooks.
 * Gracefully shuts down on SIGINT/SIGTERM.
 */
export async function runSchedule(options: ScheduleOptions): Promise<void> {
  const intervalSecs = parseInterval(options.interval);
  const notifyOn = options.notifyOn ?? "grade-drop";

  // AbortController for graceful shutdown
  const ac = new AbortController();

  const onSignal = () => {
    ac.abort();
  };
  process.on("SIGINT", onSignal);
  process.on("SIGTERM", onSignal);

  console.log(
    `${timestamp()} GoldenCheck scheduler started — scanning ${options.files.length} file(s) every ${options.interval}`,
  );

  // Dynamic import of reader (Node-only)
  let readFile: (path: string) => import("../data.js").TabularData;
  try {
    const reader = await import("../../node/reader.js");
    readFile = reader.readFile;
  } catch {
    throw new Error(
      "Scheduler requires the Node reader. Import from 'goldencheck/node' or ensure node/reader.js is available.",
    );
  }

  let runCount = 0;
  while (!ac.signal.aborted) {
    runCount += 1;
    console.log(`${timestamp()} Run #${runCount}`);

    for (const file of options.files) {
      if (ac.signal.aborted) break;

      try {
        const data = readFile(file);
        const result = scanData(data, { domain: options.domain ?? undefined });
        let findings = applyConfidenceDowngrade(result.findings, false);

        // Fix filePath in profile
        const profile = { ...result.profile, filePath: file };

        const errors = findings.filter((f) => f.severity === Severity.ERROR).length;
        const warnings = findings.filter((f) => f.severity === Severity.WARNING).length;

        // Compute health score for display and webhook
        const byCol: Record<string, { errors: number; warnings: number }> = {};
        for (const f of findings) {
          if (f.severity >= Severity.WARNING) {
            if (!byCol[f.column]) {
              byCol[f.column] = { errors: 0, warnings: 0 };
            }
            if (f.severity === Severity.ERROR) {
              byCol[f.column]!.errors += 1;
            } else {
              byCol[f.column]!.warnings += 1;
            }
          }
        }
        const hs = healthScore(byCol);

        // Record to history
        recordScan(file, profile, findings);

        // Output
        if (options.jsonOutput) {
          console.log(reportJson(findings, profile));
        } else {
          const fileName = file.split("/").pop() ?? file;
          console.log(`  ${fileName}: ${errors} errors, ${warnings} warnings (${hs.grade})`);
        }

        // Webhook notification
        if (options.webhook) {
          const prev = getPreviousScan(file);
          if (shouldNotify(hs.grade, findings, prev, notifyOn)) {
            await sendWebhook(
              options.webhook,
              file,
              hs.grade,
              hs.points,
              findings,
              notifyOn,
              prev?.grade,
            );
          }
        }
      } catch (e) {
        const fileName = file.split("/").pop() ?? file;
        console.error(`  ${fileName}: ERROR — ${e}`);
      }
    }

    // Wait for next run (check abort every second)
    const completed = await sleepWithAbort(intervalSecs, ac.signal);
    if (!completed) break;
  }

  // Cleanup signal handlers
  process.removeListener("SIGINT", onSignal);
  process.removeListener("SIGTERM", onSignal);

  console.log(`\n${timestamp()} Scheduler stopped after ${runCount} run(s).`);
}
