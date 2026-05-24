/**
 * Pipeline — high-level orchestrator + programmatic run helpers.
 * Port of goldenpipe/pipeline.py + goldenpipe/_api.py (the DataFrame paths).
 *
 * Operates on `Row[]`. The file-loading `run(source)` entry point lives in
 * `node/` (it needs `node:fs`); the edge-safe core only exposes the in-memory
 * `runDf` / `runStages` paths.
 *
 * Edge-safe: no `node:` imports.
 */

import type { PipeContext, PipelineConfig, PipeResult, Row, Stage } from "./models.js";
import { makePipeContext, makePipelineConfig, makeStageSpec, PipeStatus } from "./models.js";
import { StageRegistry } from "./engine/registry.js";
import { Resolver } from "./engine/resolver.js";
import { Runner } from "./engine/runner.js";
import { Reporter } from "./engine/reporter.js";
import { buildDefaultRegistry } from "./adapters/index.js";

const DEFAULT_STAGE_ORDER = [
  "goldencheck.scan",
  "goldenflow.transform",
  "goldenmatch.dedupe",
];

export interface PipelineOptions {
  config?: PipelineConfig | undefined;
  registry?: StageRegistry | undefined;
}

export class Pipeline {
  private readonly config: PipelineConfig | undefined;
  private readonly registry: StageRegistry;

  constructor(options?: PipelineOptions) {
    this.config = options?.config;
    // When the caller supplies a registry, use it as-is; otherwise build the
    // default suite registry (load + scan + transform + dedupe).
    this.registry = options?.registry ?? buildDefaultRegistry();
  }

  /** Run the pipeline on an array of rows. */
  async run(rows: readonly Row[], source = "<rows>"): Promise<PipeResult> {
    const ctx = makePipeContext({
      df: [...rows] as Row[],
      metadata: { source, input_rows: rows.length },
    });

    const config = this.config ?? this.autoConfig();

    let plan;
    try {
      plan = Resolver.resolve(config, this.registry);
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      return {
        status: PipeStatus.FAILED,
        source,
        inputRows: rows.length,
        stages: {},
        artifacts: {},
        skipped: [],
        errors: [`Pipeline resolution failed: ${message}`],
        reasoning: {},
        timing: {},
      };
    }

    const runner = new Runner(this.registry);
    const stages = await runner.run(plan, ctx);
    return Reporter.build(ctx, stages);
  }

  /** Build the default check→flow→dedupe config from the available stages. */
  private autoConfig(): PipelineConfig {
    const available = this.registry.listAll();
    const stages = DEFAULT_STAGE_ORDER.filter((name) => name in available).map((name) =>
      makeStageSpec(name),
    );
    return makePipelineConfig({ pipeline: "auto", stages });
  }
}

/**
 * Run a pipeline on an array of rows. Zero-config (default suite chain) or with
 * an explicit PipelineConfig. Port of `_api.run_df`.
 */
export async function runDf(
  rows: readonly Row[],
  config?: PipelineConfig,
  source = "<rows>",
): Promise<PipeResult> {
  const pipe = new Pipeline(config !== undefined ? { config } : {});
  return pipe.run(rows, source);
}

/**
 * Run specific stages programmatically against rows. Port of `_api.run_stages`.
 * The auto-prepended `load` stage is removed since rows are already supplied.
 */
export async function runStages(stages: readonly Stage[], rows: readonly Row[]): Promise<PipeResult> {
  const registry = new StageRegistry();
  for (const s of stages) registry.register(s);

  const config = makePipelineConfig({
    pipeline: "programmatic",
    stages: stages.map((s) => makeStageSpec(s.info.name)),
  });

  const ctx: PipeContext = makePipeContext({
    df: [...rows] as Row[],
    metadata: { source: "<programmatic>", input_rows: rows.length },
  });

  const plan = Resolver.resolve(config, registry);
  plan.stages = plan.stages.filter((s) => s.name !== "load");

  const runner = new Runner(registry);
  const results = await runner.run(plan, ctx);
  return Reporter.build(ctx, results);
}
