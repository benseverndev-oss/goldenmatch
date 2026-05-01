/**
 * Config loader — YAML load/save/merge.
 * YAML is an optional peer dependency.
 */

import type { GoldenFlowConfig } from "../types.js";
import { makeConfig } from "../types.js";
import { validateConfig } from "./schema.js";

let yamlModule: { parse: (s: string) => unknown; stringify: (o: unknown) => string } | null =
  null;

function getYaml(): typeof yamlModule {
  if (yamlModule) return yamlModule;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    yamlModule = require("yaml") as typeof yamlModule;
  } catch {
    // Will be handled by callers
  }
  return yamlModule;
}

export function loadConfigFromString(content: string): GoldenFlowConfig {
  const yaml = getYaml();
  if (!yaml) {
    throw new Error("yaml package is required for config loading. Install with: npm install yaml");
  }
  const data = yaml.parse(content);
  if (data === null || data === undefined) return makeConfig();
  if (typeof data !== "object" || Array.isArray(data)) {
    throw new Error(`Config file is not a valid YAML object (got ${Array.isArray(data) ? "array" : typeof data})`);
  }
  return validateConfig(data as Record<string, unknown>);
}

export function saveConfigToString(config: GoldenFlowConfig): string {
  const yaml = getYaml();
  if (!yaml) {
    throw new Error("yaml package is required for config saving. Install with: npm install yaml");
  }
  // Only include non-default values
  const data: Record<string, unknown> = {};
  if (config.source) data["source"] = config.source;
  if (config.output) data["output"] = config.output;
  if (config.transforms.length > 0) data["transforms"] = config.transforms;
  if (config.splits.length > 0) data["splits"] = config.splits;
  if (Object.keys(config.renames).length > 0) data["renames"] = config.renames;
  if (config.drop.length > 0) data["drop"] = config.drop;
  if (config.filters.length > 0) data["filters"] = config.filters;
  if (config.dedup) data["dedup"] = config.dedup;
  if (config.mappings.length > 0) data["mappings"] = config.mappings;
  return yaml.stringify(data);
}

export function mergeConfigs(
  fileConfig: GoldenFlowConfig,
  cliOverrides: Partial<GoldenFlowConfig>,
): GoldenFlowConfig {
  return makeConfig({ ...fileConfig, ...cliOverrides });
}
