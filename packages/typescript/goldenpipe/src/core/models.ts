/**
 * Core data models — port of goldenpipe/models/{context,config,stage}.py.
 *
 * Edge-safe: no `node:` imports. Data flows as `Row[]` (arrays of plain row
 * objects) instead of Polars DataFrames — the TS siblings all operate on
 * `Row[]`.
 */

/** A single tabular record. Mirrors the siblings' `Row` type. */
export type Row = Record<string, unknown>;

// ---------------------------------------------------------------------------
// Status enums (mirrors Python str-Enums; kept as string-literal unions in TS)
// ---------------------------------------------------------------------------

export const StageStatus = {
  SUCCESS: "success",
  SKIPPED: "skipped",
  FAILED: "failed",
} as const;
export type StageStatus = (typeof StageStatus)[keyof typeof StageStatus];

export const PipeStatus = {
  SUCCESS: "success",
  PARTIAL: "partial",
  FAILED: "failed",
} as const;
export type PipeStatus = (typeof PipeStatus)[keyof typeof PipeStatus];

// ---------------------------------------------------------------------------
// Decision — routing instruction from a stage to the framework
// ---------------------------------------------------------------------------

export interface Decision {
  /** Stage names to drop from the remaining plan. */
  skip: string[];
  /** Abort the whole pipeline after this stage. */
  abort: boolean;
  /** Stage names to prepend to the remaining plan. */
  insert: string[];
  /** Human-readable explanation, surfaced in `reasoning._router`. */
  reason: string;
}

/** Construct a Decision with Python-parity defaults. */
export function makeDecision(input?: Partial<Decision>): Decision {
  return {
    skip: input?.skip ?? [],
    abort: input?.abort ?? false,
    insert: input?.insert ?? [],
    reason: input?.reason ?? "",
  };
}

// ---------------------------------------------------------------------------
// StageResult — returned by every stage's run()
// ---------------------------------------------------------------------------

export interface StageResult {
  status: StageStatus;
  decision?: Decision | null;
  error?: string | null;
}

// ---------------------------------------------------------------------------
// PipeContext — the object flowing through the pipeline (mutated in place)
// ---------------------------------------------------------------------------

export interface PipeContext {
  /** Working data. `null` until the load stage populates it. */
  df: Row[] | null;
  artifacts: Record<string, unknown>;
  metadata: Record<string, unknown>;
  timing: Record<string, number>;
  reasoning: Record<string, string>;
  /** Per-stage config made available to the adapter by the runner. */
  stageConfig: Record<string, unknown>;
}

export function makePipeContext(input?: Partial<PipeContext>): PipeContext {
  return {
    df: input?.df ?? null,
    artifacts: input?.artifacts ?? {},
    metadata: input?.metadata ?? {},
    timing: input?.timing ?? {},
    reasoning: input?.reasoning ?? {},
    stageConfig: input?.stageConfig ?? {},
  };
}

// ---------------------------------------------------------------------------
// PipeResult — final output returned to the caller
// ---------------------------------------------------------------------------

export interface PipeResult {
  status: PipeStatus;
  source: string;
  inputRows: number;
  stages: Record<string, StageResult>;
  artifacts: Record<string, unknown>;
  skipped: string[];
  errors: string[];
  reasoning: Record<string, string>;
  timing: Record<string, number>;
}

// ---------------------------------------------------------------------------
// Stage configuration models (mirrors models/config.py Pydantic models)
// ---------------------------------------------------------------------------

export type OnError = "continue" | "abort";

export interface StageSpec {
  name?: string | undefined;
  use: string;
  needs: string[];
  skipIf?: string | undefined;
  onError: OnError;
  config: Record<string, unknown>;
}

/** Normalize a raw stage spec (object or bare string) into a full StageSpec. */
export function makeStageSpec(input: string | Partial<StageSpec> & { use: string }): StageSpec {
  if (typeof input === "string") {
    return { use: input, needs: [], onError: "continue", config: {} };
  }
  return {
    ...(input.name !== undefined ? { name: input.name } : {}),
    use: input.use,
    needs: input.needs ?? [],
    ...(input.skipIf !== undefined ? { skipIf: input.skipIf } : {}),
    onError: input.onError ?? "continue",
    config: input.config ?? {},
  };
}

export interface PipelineConfig {
  pipeline: string;
  source?: string | undefined;
  output?: string | undefined;
  /** Stages may be bare strings or full specs; normalize via `makeStageSpec`. */
  stages: Array<string | StageSpec>;
  decisions: string[];
}

export function makePipelineConfig(
  input: Partial<PipelineConfig> & { pipeline: string; stages: Array<string | StageSpec> },
): PipelineConfig {
  return {
    pipeline: input.pipeline,
    ...(input.source !== undefined ? { source: input.source } : {}),
    ...(input.output !== undefined ? { output: input.output } : {}),
    stages: input.stages,
    decisions: input.decisions ?? [],
  };
}

// ---------------------------------------------------------------------------
// Stage protocol + StageInfo + @stage factory (mirrors models/stage.py)
// ---------------------------------------------------------------------------

export interface StageInfo {
  name: string;
  produces: string[];
  consumes: string[];
}

/**
 * Full contract for pipeline stages. `run` is async so it can await the
 * async GoldenMatch `dedupe` adapter.
 */
export interface Stage {
  readonly info: StageInfo;
  validate(ctx: PipeContext): void | Promise<void>;
  run(ctx: PipeContext): Promise<StageResult>;
  rollback?: ((ctx: PipeContext) => void | Promise<void>) | null;
}

/**
 * Wrap a plain async function into a Stage. Port of the Python `@stage`
 * decorator + `_FunctionStage`.
 */
export function stage(
  info: StageInfo,
  fn: (ctx: PipeContext) => Promise<StageResult> | StageResult,
): Stage {
  return {
    info,
    validate(_ctx: PipeContext): void {
      /* function stages have no validation hook */
    },
    async run(ctx: PipeContext): Promise<StageResult> {
      return fn(ctx);
    },
    rollback: null,
  };
}
