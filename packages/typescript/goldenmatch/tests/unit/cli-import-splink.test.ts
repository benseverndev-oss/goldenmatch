/**
 * cli-import-splink.test.ts -- Task T4: the `import-splink` CLI subcommand.
 *
 * Per repo convention (cli-memory.test.ts / cli-suggest.test.ts) we drive the
 * extracted, writer-injected `runImportSplinkCli` directly rather than the
 * commander tree, using real tmp files (the box constraint means we can't
 * build dist/ and shell out).
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtempSync, rmSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { runImportSplinkCli } from "../../src/node/cli-import-splink.js";
import { parseConfigYaml } from "../../src/core/config/loader.js";
import { emResultFromJson } from "../../src/core/probabilistic.js";
import { parse as parseYaml } from "yaml";

let dir: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "gm-cli-import-splink-"));
});

afterEach(() => {
  try {
    rmSync(dir, { recursive: true, force: true });
  } catch {
    /* ignore */
  }
});

function writers() {
  const out: string[] = [];
  const err: string[] = [];
  return { out, err, w: { out: (s: string) => out.push(s), err: (s: string) => err.push(s) } };
}

function bareSettings(): Record<string, unknown> {
  return {
    comparisons: [
      {
        output_column_name: "first_name",
        comparison_levels: [
          { sql_condition: '"first_name_l" IS NULL OR "first_name_r" IS NULL', is_null_level: true },
          { sql_condition: '"first_name_l" = "first_name_r"' },
          { sql_condition: "ELSE" },
        ],
      },
    ],
    blocking_rules_to_generate_predictions: ['l."first_name" = r."first_name"'],
  };
}

function trainedSettings(): Record<string, unknown> {
  return {
    comparisons: [
      {
        output_column_name: "first_name",
        comparison_levels: [
          { sql_condition: '"first_name_l" IS NULL OR "first_name_r" IS NULL', is_null_level: true },
          { sql_condition: '"first_name_l" = "first_name_r"', m_probability: 0.8, u_probability: 0.02 },
          { sql_condition: "ELSE", m_probability: 0.2, u_probability: 0.98 },
        ],
      },
    ],
    blocking_rules_to_generate_predictions: ['l."first_name" = r."first_name"'],
    probability_two_random_records_match: 0.001,
  };
}

// Mixed bare/trained: field "surname" carries no m/u while "first_name" does
// -> a partial model that must be refused for --model-out.
function mixedSettings(): Record<string, unknown> {
  const trained = (trainedSettings()["comparisons"] as unknown[])[0];
  const bareSurname = {
    output_column_name: "surname",
    comparison_levels: [
      { sql_condition: '"surname_l" IS NULL OR "surname_r" IS NULL', is_null_level: true },
      { sql_condition: '"surname_l" = "surname_r"' },
      { sql_condition: "ELSE" },
    ],
  };
  return {
    comparisons: [trained, bareSurname],
    blocking_rules_to_generate_predictions: ['l."first_name" = r."first_name"'],
    probability_two_random_records_match: 0.001,
  };
}

describe("runImportSplinkCli — happy path (bare settings)", () => {
  it("writes a valid GoldenMatch YAML config and prints a summary", () => {
    const inputPath = join(dir, "settings.json");
    const outputPath = join(dir, "goldenmatch.yaml");
    writeFileSync(inputPath, JSON.stringify(bareSettings()), "utf8");

    const w = writers();
    const code = runImportSplinkCli(inputPath, { output: outputPath }, w.w);

    expect(code).toBe(0);
    expect(existsSync(outputPath)).toBe(true);
    const yamlText = readFileSync(outputPath, "utf8");
    const config = parseConfigYaml(yamlText, parseYaml);
    expect(config.matchkeys?.[0]?.type).toBe("probabilistic");
    expect(w.out.join("")).toContain("Wrote config to");
    expect(w.out.join("")).toContain("error(s)");
  });
});

describe("runImportSplinkCli — bad input", () => {
  it("exits 1 with a clean message on missing file (no config written)", () => {
    const inputPath = join(dir, "does-not-exist.json");
    const outputPath = join(dir, "goldenmatch.yaml");
    const w = writers();
    const code = runImportSplinkCli(inputPath, { output: outputPath }, w.w);
    expect(code).toBe(1);
    expect(existsSync(outputPath)).toBe(false);
    expect(w.err.join("")).toMatch(/Could not read Splink input file/);
  });

  it("exits 1 with a clean message on malformed JSON (no config written)", () => {
    const inputPath = join(dir, "settings.json");
    const outputPath = join(dir, "goldenmatch.yaml");
    writeFileSync(inputPath, "{not valid json", "utf8");
    const w = writers();
    const code = runImportSplinkCli(inputPath, { output: outputPath }, w.w);
    expect(code).toBe(1);
    expect(existsSync(outputPath)).toBe(false);
    expect(w.err.join("")).toMatch(/not valid JSON/);
  });

  it("exits 1 on zero-convertible-comparisons (SplinkConversionError), no files written", () => {
    const inputPath = join(dir, "settings.json");
    const outputPath = join(dir, "goldenmatch.yaml");
    writeFileSync(inputPath, JSON.stringify({ comparisons: [] }), "utf8");
    const w = writers();
    const code = runImportSplinkCli(inputPath, { output: outputPath }, w.w);
    expect(code).toBe(1);
    expect(existsSync(outputPath)).toBe(false);
    expect(w.err.join("")).toContain("Splink conversion failed");
  });
});

describe("runImportSplinkCli — strict mode", () => {
  it("exits 1 when strict and the input carries lossy findings", () => {
    // A dropped OR-blocking-rule after the single usable one -> a warning
    // finding (unrecognized blocking rule) that strict mode gates on... use
    // an unrecognized comparison-level SQL to force a warning finding.
    const settings = bareSettings();
    (settings["comparisons"] as Record<string, unknown>[])[0]!["comparison_levels"] = [
      ...((settings["comparisons"] as Record<string, unknown>[])[0]!["comparison_levels"] as unknown[]),
      { sql_condition: "some_weird_condition(x, y)" },
    ];
    const inputPath = join(dir, "settings.json");
    const outputPath = join(dir, "goldenmatch.yaml");
    writeFileSync(inputPath, JSON.stringify(settings), "utf8");
    const w = writers();
    const code = runImportSplinkCli(inputPath, { output: outputPath, strict: true }, w.w);
    expect(code).toBe(1);
    expect(existsSync(outputPath)).toBe(false);
    expect(w.err.join("")).toContain("Splink conversion failed");
  });
});

describe("runImportSplinkCli — trained model + --model-out (cross-surface file)", () => {
  it("writes config with matchkeys[0].model_path set, and a model file emResultFromJson can read back", () => {
    const inputPath = join(dir, "settings.json");
    const outputPath = join(dir, "goldenmatch.yaml");
    const modelOutPath = join(dir, "model.json");
    writeFileSync(inputPath, JSON.stringify(trainedSettings()), "utf8");

    const w = writers();
    const code = runImportSplinkCli(
      inputPath,
      { output: outputPath, modelOut: modelOutPath },
      w.w,
    );

    expect(code).toBe(0);
    expect(existsSync(outputPath)).toBe(true);
    expect(existsSync(modelOutPath)).toBe(true);

    const config = parseConfigYaml(readFileSync(outputPath, "utf8"), parseYaml);
    const mk = config.matchkeys?.[0];
    expect(mk?.type).toBe("probabilistic");
    expect((mk as { modelPath?: string }).modelPath).toBe(modelOutPath);

    const modelJson = JSON.parse(readFileSync(modelOutPath, "utf8"));
    expect(modelJson.__type__).toBe("goldenmatch.EMResult");
    const em = emResultFromJson(modelJson);
    expect(em.matchWeights["first_name"]).toBeDefined();
    expect(w.out.join("")).toContain("Trained model persisted to");
  });

  it("prints a dropped-probabilities warning when trained but no --model-out is given", () => {
    const inputPath = join(dir, "settings.json");
    const outputPath = join(dir, "goldenmatch.yaml");
    writeFileSync(inputPath, JSON.stringify(trainedSettings()), "utf8");

    const w = writers();
    const code = runImportSplinkCli(inputPath, { output: outputPath }, w.w);

    expect(code).toBe(0);
    const config = parseConfigYaml(readFileSync(outputPath, "utf8"), parseYaml);
    expect((config.matchkeys?.[0] as { modelPath?: string }).modelPath).toBeUndefined();
    expect(w.out.join("")).toMatch(/NOT persisted/);
  });
});

describe("runImportSplinkCli — partial-model refusal (mixed bare/trained)", () => {
  it("writes the config WITHOUT model_path, writes no model file, and exits 1", () => {
    const inputPath = join(dir, "settings.json");
    const outputPath = join(dir, "goldenmatch.yaml");
    const modelOutPath = join(dir, "model.json");
    writeFileSync(inputPath, JSON.stringify(mixedSettings()), "utf8");

    const w = writers();
    const code = runImportSplinkCli(
      inputPath,
      { output: outputPath, modelOut: modelOutPath },
      w.w,
    );

    expect(code).toBe(1);
    expect(existsSync(outputPath)).toBe(true);
    expect(existsSync(modelOutPath)).toBe(false);

    const config = parseConfigYaml(readFileSync(outputPath, "utf8"), parseYaml);
    expect((config.matchkeys?.[0] as { modelPath?: string }).modelPath).toBeUndefined();
    expect(w.err.join("")).toContain("--model-out refused");
    expect(w.err.join("")).toContain("surname");
  });
});
