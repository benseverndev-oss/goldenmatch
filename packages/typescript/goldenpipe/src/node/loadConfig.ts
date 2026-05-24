/**
 * YAML config loading and normalization.
 * Port of goldenpipe/config/loader.py.
 *
 * Node-only: reads a file and parses YAML via the optional `yaml` peer dep.
 */

import { readFileSync } from "node:fs";
import type { PipelineConfig, StageSpec } from "../core/index.js";
import { makePipelineConfig, makeStageSpec } from "../core/index.js";

/** Load and validate a pipeline config from a YAML file path. */
export async function loadConfig(path: string): Promise<PipelineConfig> {
  let parse: (src: string) => unknown;
  try {
    // `as string` prevents tsup from eagerly resolving the optional peer dep.
    const yamlMod = (await import("yaml" as string)) as { parse: (s: string) => unknown };
    parse = yamlMod.parse;
  } catch {
    throw new Error(
      "YAML support requires the optional `yaml` peer dependency. Run: npm install yaml",
    );
  }

  let content: string;
  try {
    content = readFileSync(path, "utf8");
  } catch {
    throw new Error(`Config file not found: ${path}`);
  }

  const raw = parse(content);
  return normalizeConfig(raw);
}

/** Normalize a parsed YAML object into a validated PipelineConfig. */
export function normalizeConfig(raw: unknown): PipelineConfig {
  if (raw === null || typeof raw !== "object") {
    throw new Error("Pipeline config must be a mapping");
  }
  const obj = raw as Record<string, unknown>;

  if (typeof obj["pipeline"] !== "string") {
    throw new Error("Pipeline config must have a string 'pipeline' field");
  }

  const rawStages = obj["stages"] ?? [];
  if (!Array.isArray(rawStages)) {
    throw new Error(`'stages' must be a list, got: ${typeof rawStages}`);
  }

  const stages: Array<string | StageSpec> = [];
  for (const s of rawStages) {
    if (typeof s === "string") {
      stages.push(makeStageSpec(s));
    } else if (s !== null && typeof s === "object") {
      const so = s as Record<string, unknown>;
      if (!("use" in so) && !("name" in so)) {
        throw new Error(`Stage spec must have 'use' field: ${JSON.stringify(s)}`);
      }
      const use = (so["use"] ?? so["name"]) as string;
      stages.push(
        makeStageSpec({
          use,
          ...(typeof so["name"] === "string" ? { name: so["name"] } : {}),
          ...(Array.isArray(so["needs"]) ? { needs: so["needs"] as string[] } : {}),
          ...(typeof so["skip_if"] === "string" ? { skipIf: so["skip_if"] } : {}),
          ...(typeof so["skipIf"] === "string" ? { skipIf: so["skipIf"] } : {}),
          ...(so["on_error"] === "abort" || so["on_error"] === "continue"
            ? { onError: so["on_error"] }
            : {}),
          ...(so["onError"] === "abort" || so["onError"] === "continue"
            ? { onError: so["onError"] }
            : {}),
          ...(so["config"] !== null && typeof so["config"] === "object"
            ? { config: so["config"] as Record<string, unknown> }
            : {}),
        }),
      );
    } else {
      throw new Error(`Invalid stage spec: ${JSON.stringify(s)}`);
    }
  }

  return makePipelineConfig({
    pipeline: obj["pipeline"],
    ...(typeof obj["source"] === "string" ? { source: obj["source"] } : {}),
    ...(typeof obj["output"] === "string" ? { output: obj["output"] } : {}),
    stages,
    ...(Array.isArray(obj["decisions"]) ? { decisions: obj["decisions"] as string[] } : {}),
  });
}
