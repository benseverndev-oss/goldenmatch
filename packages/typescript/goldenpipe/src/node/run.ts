/**
 * Node `run(source)` — load a CSV file into rows and run the pipeline.
 * Port of goldenpipe/_api.run (the file path) + pipeline.py CSV loading.
 *
 * Node-only: reads from the filesystem.
 */

import type { PipelineConfig, PipeResult } from "../core/index.js";
import { Pipeline, PipeStatus } from "../core/index.js";
import { readCsv } from "./csv.js";
import { loadConfig } from "./loadConfig.js";

export interface RunOptions {
  /** Path to a YAML pipeline config. When set, it wins over the default chain. */
  config?: string | PipelineConfig | undefined;
}

/** Run a pipeline on a CSV file. Zero-config or from a YAML config path. */
export async function run(source: string, options?: RunOptions): Promise<PipeResult> {
  let rows;
  try {
    rows = readCsv(source);
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    return {
      status: PipeStatus.FAILED,
      source,
      inputRows: 0,
      stages: {},
      artifacts: {},
      skipped: [],
      errors: [`Failed to load data: ${message}`],
      reasoning: {},
      timing: {},
    };
  }

  let config: PipelineConfig | undefined;
  const cfgOpt = options?.config;
  if (typeof cfgOpt === "string") {
    config = await loadConfig(cfgOpt);
  } else if (cfgOpt !== undefined) {
    config = cfgOpt;
  }

  const pipe = new Pipeline(config !== undefined ? { config } : {});
  return pipe.run(rows, source);
}
