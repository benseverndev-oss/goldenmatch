#!/usr/bin/env node
// CLI for the TypeScript benchmark runner.
// Mirrors infermap_bench/cli.py. Note: rebuild-manifest and
// regenerate-synthetic are NOT implemented here — Python is the canonical
// manifest/synthetic generator per spec §7.4. Use the Python CLI for those.

import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Command } from "commander";

import { RUNNER_VERSION, MANIFEST_VERSION } from "./index.js";
import { loadManifest, IncompatibleManifestError, InvalidManifestError } from "./manifest.js";
import { loadCase } from "./cases.js";
import { loadSyntheticCases } from "./synthetic.js";
import { runCases } from "./runner.js";
import { buildReport, writeReport, type Report } from "./report.js";
import { computeDelta } from "./compare.js";
import type { CaseRef, CaseData } from "./types.js";

// ---------------------------------------------------------------------------
// Path constants
// ---------------------------------------------------------------------------

const __filename_ =
  typeof __filename !== "undefined" ? __filename : fileURLToPath(import.meta.url);
const __dirname_ = typeof __dirname !== "undefined" ? __dirname : dirname(__filename_);

export const REPO_ROOT = resolve(__dirname_, "../../../..");
export const BENCHMARK_ROOT = resolve(REPO_ROOT, "benchmark");
export const SELF_TEST_ROOT = resolve(BENCHMARK_ROOT, "self-test");

// ---------------------------------------------------------------------------
// Helpers (exported for testing)
// ---------------------------------------------------------------------------

export function matchesFilter(ref: CaseRef, filter: string): boolean {
  if (!filter.includes(":")) {
    return ref.id.startsWith(filter);
  }
  const sep = filter.indexOf(":");
  const key = filter.slice(0, sep);
  const value = filter.slice(sep + 1);
  if (key === "category") return ref.category === value;
  if (key === "difficulty") return ref.expectedDifficulty === value;
  if (key === "tag") return ref.tags.includes(value);
  return false;
}

function readInfermapVersion(): string {
  // Returns "unknown" on failure (NOT "0.0.0", which would silently corrupt
  // the baseline by tagging real runs with a fake version). Errors are logged
  // to stderr so CI surfaces them.
  try {
    const pkg = JSON.parse(
      readFileSync(resolve(REPO_ROOT, "packages/infermap-js/package.json"), "utf8"),
    ) as { version: string };
    return pkg.version;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    process.stderr.write(`warn: failed to read infermap-js package.json: ${msg}\n`);
    return "unknown";
  }
}

function loadSyntheticCasesIfExists(): CaseData[] {
  const path = resolve(BENCHMARK_ROOT, "cases/synthetic/generated.json");
  if (!existsSync(path)) {
    process.stderr.write(`note: ${path} not found — skipping synthetic cases\n`);
    return [];
  }
  return loadSyntheticCases(path);
}

// Tolerance is intentionally 1e-4 (not tighter): expected_self_test.json
// stores metrics rounded to 6 decimal places (e.g. f1=0.857143 is 6/7
// rounded). 1e-6 would trip on that rounding. If the expected file is ever
// regenerated at higher precision, tighten this.
function assertScorecardMatches(
  actual: Report,
  expected: { scorecard: { overall: Record<string, number> } },
  tolerance = 1e-4,
): void {
  const a = actual.scorecard.overall as unknown as Record<string, number>;
  const e = expected.scorecard.overall;
  const keys = ["f1", "top1", "mrr", "ece"] as const;
  for (const key of keys) {
    const av = a[key] ?? 0;
    const ev = e[key] ?? 0;
    if (Math.abs(av - ev) > tolerance) {
      process.stderr.write(`MISMATCH on ${key}: actual=${av} expected=${ev}\n`);
      process.exit(1);
    }
  }
  console.log("scorecard matches expected (within tolerance)");
}

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

const program = new Command();
program
  .name("infermap-bench")
  .description(
    "TypeScript benchmark runner for infermap.\n\n" +
      "Note: rebuild-manifest and regenerate-synthetic are Python-only — " +
      "use `python -m infermap_bench <cmd>` for those.",
  )
  .version(RUNNER_VERSION);

program
  .command("run")
  .description("Run the benchmark and write a report.json")
  .option("--output <path>", "report output path", "ts-report.json")
  .option("--seed <n>", "seed (unused but kept for symmetry with Python)", "42")
  .option("--only <filter>", "filter cases by prefix or slice")
  .option("--self-test", "run against self-test corpus instead of full benchmark")
  .option("--assert-against <path>", "expected scorecard file for assertion")
  .action(
    (opts: {
      output: string;
      seed: string;
      only?: string;
      selfTest?: boolean;
      assertAgainst?: string;
    }) => {
      const root = opts.selfTest ? SELF_TEST_ROOT : BENCHMARK_ROOT;
      const manifestPath = resolve(root, "manifest.json");

      let refs: CaseRef[] = [];
      if (existsSync(manifestPath)) {
        try {
          refs = loadManifest(manifestPath);
        } catch (e) {
          if (
            e instanceof IncompatibleManifestError ||
            e instanceof InvalidManifestError
          ) {
            process.stderr.write(`manifest error: ${e.message}\n`);
            process.exit(1);
          }
          throw e;
        }
      } else {
        process.stderr.write(`manifest not found at ${manifestPath}\n`);
      }

      if (opts.only) {
        refs = refs.filter((r) => matchesFilter(r, opts.only!));
      }

      const cases: CaseData[] = refs.map((ref) => loadCase(root, ref));

      if (!opts.selfTest) {
        cases.push(...loadSyntheticCasesIfExists());
      }

      // Refuse to write a "perfect" scorecard for an empty corpus — it would
      // poison baselines. An empty run is almost always a misconfiguration.
      if (cases.length === 0) {
        process.stderr.write(
          "ERROR: no cases to run. Check manifest path and --only filter. " +
            "Refusing to write a vacuous scorecard.\n",
        );
        process.exit(2);
      }

      const t0 = performance.now();
      const results = runCases(cases);
      const duration = (performance.now() - t0) / 1000;

      const report = buildReport(results, {
        language: "typescript",
        infermapVersion: readInfermapVersion(),
        runnerVersion: RUNNER_VERSION,
        durationSeconds: duration,
      });
      const schemaPath = resolve(BENCHMARK_ROOT, "report.schema.json");
      writeReport(report, opts.output, schemaPath);
      console.log(`wrote ${opts.output} (${results.length} cases)`);

      if (opts.assertAgainst) {
        const expected = JSON.parse(readFileSync(opts.assertAgainst, "utf8"));
        assertScorecardMatches(report, expected);
      }
    },
  );

program
  .command("compare")
  .description("Compare two report.json files and print the delta")
  .requiredOption("--baseline <path>", "baseline report.json")
  .requiredOption("--current <path>", "current report.json")
  .action((opts: { baseline: string; current: string }) => {
    const base = JSON.parse(readFileSync(opts.baseline, "utf8"));
    const curr = JSON.parse(readFileSync(opts.current, "utf8"));
    const delta = computeDelta(base, curr);
    const signed = (n: number): string =>
      n >= 0 ? `+${n.toFixed(4)}` : n.toFixed(4);
    console.log(`F1 delta:    ${signed(delta.overall.f1 ?? 0)}`);
    console.log(`top-1 delta: ${signed(delta.overall.top1 ?? 0)}`);
    console.log(`MRR delta:   ${signed(delta.overall.mrr ?? 0)}`);
    console.log(`ECE delta:   ${signed(delta.overall.ece ?? 0)}`);
    console.log(`Regression (threshold 0.02): ${delta.isRegression(0.02)}`);
  });

program
  .command("report <path>")
  .description("Pretty-print a report.json to stdout")
  .action((path: string) => {
    const data = JSON.parse(readFileSync(path, "utf8"));
    const sc = data.scorecard.overall;
    console.log(`language: ${data.language}`);
    console.log(`infermap: ${data.infermap_version}`);
    console.log(`duration: ${data.duration_seconds}s`);
    console.log(`cases:    ${sc.n}`);
    console.log(`F1:       ${sc.f1.toFixed(4)}`);
    console.log(`top-1:    ${sc.top1.toFixed(4)}`);
    console.log(`MRR:      ${sc.mrr.toFixed(4)}`);
    console.log(`ECE:      ${sc.ece.toFixed(4)}`);
  });

program
  .command("migrate")
  .description(
    "Migrate manifest.json / case.json / expected.json between contract versions (stub)",
  )
  .requiredOption("--from <n>", "current manifest version", (v) => parseInt(v, 10))
  .requiredOption("--to <n>", "target manifest version", (v) => parseInt(v, 10))
  .option("--dry-run", "report what would change without writing")
  .action((opts: { from: number; to: number; dryRun?: boolean }) => {
    if (opts.from === MANIFEST_VERSION && opts.to === MANIFEST_VERSION) {
      console.log(`No migration needed — already at version ${MANIFEST_VERSION}`);
      return;
    }
    if (opts.to > MANIFEST_VERSION) {
      process.stderr.write(
        `ERROR: target version ${opts.to} exceeds what this runner supports ` +
          `(${MANIFEST_VERSION}). Upgrade @infermap/bench first.\n`,
      );
      process.exit(1);
    }
    throw new Error(
      `Migration from v${opts.from} to v${opts.to} is not implemented. ` +
        `Add a migration step in src/cli.ts migrate command per spec §6.6.`,
    );
  });

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

const argv1 = process.argv[1] ?? "";
if (argv1.endsWith("cli.cjs") || argv1.endsWith("cli.js")) {
  program.parseAsync(process.argv).catch((err) => {
    console.error(err instanceof Error ? err.message : String(err));
    process.exit(1);
  });
}
