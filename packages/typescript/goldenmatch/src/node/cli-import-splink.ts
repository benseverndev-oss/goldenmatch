/**
 * cli-import-splink.ts -- the `import-splink` CLI subcommand logic.
 *
 * Mirrors `goldenmatch/cli/import_splink.py` exactly: reads a Splink
 * settings/trained-model JSON file from disk, converts it via `fromSplink()`
 * (edge-safe core, no file I/O of its own), then writes the GoldenMatch YAML
 * config and (optionally) the persisted EM model -- in that order, so a
 * failed model write never orphans a config that references a missing
 * model_path, and a failed config write never leaves a stray model file
 * behind.
 *
 * Writer-injected (like `cli-healer.ts`) so this is unit-testable without
 * driving the commander tree or shelling out to `dist/`.
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";

import { fromSplink, SplinkConversionError, type ConversionFinding } from "../core/config/from-splink.js";
import { emResultToJson } from "../core/probabilistic.js";
import { stringifyConfigYaml } from "./config-file.js";
import type { GoldenMatchConfig, MatchkeyConfig, ProbabilisticMatchkey } from "../core/types.js";

export interface ImportSplinkCliOptions {
  readonly output: string;
  readonly modelOut?: string;
  readonly strict?: boolean;
}

export interface ImportSplinkWriters {
  readonly out: (s: string) => void;
  readonly err: (s: string) => void;
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function sortKeysDeep(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeysDeep);
  if (value !== null && typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(obj).sort()) {
      out[key] = sortKeysDeep(obj[key]);
    }
    return out;
  }
  return value;
}

/** JSON.stringify with recursively sorted keys -- matches Python's
 *  `json.dump(..., indent=2, sort_keys=True)` byte-for-byte on key order. */
function stringifySorted(value: unknown): string {
  return JSON.stringify(sortKeysDeep(value), null, 2);
}

function renderFindingsTable(findings: readonly ConversionFinding[]): string {
  if (findings.length === 0) return "";
  const headers = ["Severity", "Splink Path", "Message", "Mapped To"];
  const rows = findings.map((f) => [f.severity, f.splinkPath, f.message, f.mappedTo ?? ""]);
  const widths = headers.map((h, i) => Math.max(h.length, ...rows.map((r) => r[i]!.length)));
  const pad = (s: string, w: number) => s + " ".repeat(Math.max(0, w - s.length));
  const line = (cells: readonly string[]) => cells.map((c, i) => pad(c, widths[i]!)).join("  ");
  const sep = widths.map((w) => "-".repeat(w)).join("  ");
  return (
    "Splink Conversion Findings\n" +
    `${line(headers)}\n${sep}\n` +
    rows.map((r) => line(r)).join("\n") +
    "\n"
  );
}

function withModelPath(mk: MatchkeyConfig, modelPath: string): MatchkeyConfig {
  // fromSplink() always emits matchkeys[0] as type "probabilistic" -- model_path
  // is a probabilistic-only concept (Splink-style train-once -> reuse).
  if (mk.type !== "probabilistic") return mk;
  const patched: ProbabilisticMatchkey = { ...mk, modelPath };
  return patched;
}

// ---------------------------------------------------------------------------
// entry point
// ---------------------------------------------------------------------------

/**
 * Run `import-splink`. Returns the process exit code (0 success, 1 failure)
 * rather than calling `process.exit` itself, so callers (tests, the
 * commander action) control the process lifecycle.
 */
export function runImportSplinkCli(
  inputPath: string,
  opts: ImportSplinkCliOptions,
  writers: ImportSplinkWriters,
): number {
  const resolvedInput = resolve(inputPath);
  let raw: string;
  try {
    raw = readFileSync(resolvedInput, "utf8");
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    writers.err(`Could not read Splink input file ${resolvedInput}: ${msg}\n`);
    return 1;
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    writers.err(`${resolvedInput} is not valid JSON: ${msg}\n`);
    return 1;
  }

  let conversion;
  try {
    conversion = fromSplink(parsed, { strict: opts.strict ?? false });
  } catch (err) {
    if (err instanceof SplinkConversionError) {
      writers.err(`Splink conversion failed: ${err.message}\n`);
      return 1;
    }
    throw err;
  }

  const output = opts.output;
  const modelOut = opts.modelOut;
  const matchkeysIn: readonly MatchkeyConfig[] = conversion.config.matchkeys ?? [];
  const mk0 = matchkeysIn[0]!;

  // Ordering: set model_path in-memory first, write the YAML config, THEN
  // persist the model -- a failed YAML write must not leave an orphaned
  // model.json behind.
  let persistModel = conversion.emModel !== null && Boolean(modelOut);

  // Partial-model guard: mixed bare/trained Splink input yields a model that
  // does not cover every converted field. Shipping that config+model pair
  // would fail at runtime with a misleading FSModelMismatchError ("matchkey
  // changed since training"), so refuse --model-out instead: the config is
  // still written, WITHOUT model_path (re-trains via EM).
  let missingFields: string[] = [];
  if (persistModel && conversion.emModel !== null) {
    const covered = new Set(Object.keys(conversion.emModel.matchWeights));
    missingFields = mk0.fields.map((f) => f.field).filter((f) => f && !covered.has(f));
    if (missingFields.length > 0) persistModel = false;
  }

  let matchkeys: readonly MatchkeyConfig[] = matchkeysIn;
  if (conversion.emModel !== null) {
    if (persistModel && modelOut) {
      matchkeys = [withModelPath(mk0, modelOut), ...matchkeys.slice(1)];
    } else if (!modelOut) {
      writers.out(
        "Warning: the Splink input carried trained m/u probabilities, but they were " +
          "NOT persisted -- pass --model-out <path> to keep them. The output config " +
          "will re-train via EM on first run instead.\n",
      );
    }
  }

  const finalConfig: GoldenMatchConfig = { ...conversion.config, matchkeys };

  const resolvedOutput = resolve(output);
  try {
    writeFileSync(resolvedOutput, stringifyConfigYaml(finalConfig), "utf8");
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    writers.err(`Could not write config to ${output}: ${msg}\n`);
    return 1;
  }

  if (missingFields.length > 0) {
    writers.err(
      `--model-out refused: the imported Splink model does not cover field(s) ` +
        `${missingFields.join(", ")} of matchkeys[0] (mixed bare/trained input). ` +
        "A partial model would fail FS model validation at runtime. The config " +
        `was written to ${output} WITHOUT model_path; it will re-train via EM on ` +
        "first run. No model file was written.\n",
    );
    return 1;
  }

  if (persistModel && modelOut && conversion.emModel !== null) {
    const resolvedModelOut = resolve(modelOut);
    try {
      const dir = dirname(resolvedModelOut);
      if (dir && dir !== "." && !existsSync(dir)) mkdirSync(dir, { recursive: true });
      writeFileSync(resolvedModelOut, stringifySorted(emResultToJson(conversion.emModel)), "utf8");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      writers.err(
        `Could not write trained model to ${modelOut}: ${msg}. Note: the config ` +
          `written to ${output} references this model via matchkeys[0].model_path, ` +
          "but the model file failed to write.\n",
      );
      return 1;
    }
    writers.out(`Trained model persisted to ${modelOut} (set as matchkeys[0].model_path).\n`);
  }

  const table = renderFindingsTable(conversion.report.findings);
  if (table) writers.out(table);
  writers.out(`Wrote config to ${output}. ${conversion.report.summary()}\n`);
  return 0;
}
