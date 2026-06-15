/**
 * skills.ts — AGENT_SKILLS registry + dispatchSkill (the shared dispatcher).
 * Edge-safe: no `node:` imports. The I/O seam is dependency injection
 * (`ctx.loadTable`), never a file read here.
 *
 * Wave 1 registers only the AgentSession-backed skills. MCP/A2A wiring, file
 * loaders, and optional-dependency tools (scan_quality, run_transforms, ...)
 * land in Waves 2-4 — the registry is shaped so they slot in later.
 */

import type { Row } from "../types.js";
import type { SkillDef, SkillContext, SkillResult, JSONSchema } from "./types.js";
import { profileForAgent, selectStrategy } from "./strategy.js";

// ---------------------------------------------------------------------------
// rows-or-path seam
// ---------------------------------------------------------------------------

/**
 * Resolve a table from `args.rows` (inline, edge path) or — only when rows are
 * absent — from `ctx.loadTable(args.file_path)`. Throws a clear error when
 * neither is usable (caught by the dispatcher).
 */
async function resolveTable(
  args: Record<string, unknown>,
  ctx: SkillContext,
  rowsKey = "rows",
  pathKey = "file_path",
): Promise<Row[]> {
  const inline = args[rowsKey];
  if (Array.isArray(inline)) {
    return inline as Row[];
  }
  const path = args[pathKey];
  if (typeof path === "string") {
    return ctx.loadTable(path);
  }
  // No inline rows and no path: still try loadTable so an injected loader can
  // raise the surface-specific "no loader" error (matches the test contract).
  return ctx.loadTable(typeof path === "string" ? path : "");
}

// ---------------------------------------------------------------------------
// Shared input schemas (reused verbatim by the MCP Tool + A2A card in Wave 2/3)
// ---------------------------------------------------------------------------

const ROWS_OR_PATH_SCHEMA: JSONSchema = {
  type: "object",
  properties: {
    rows: {
      type: "array",
      description: "Inline row objects (edge path). Takes precedence over file_path.",
      items: { type: "object" },
    },
    file_path: {
      type: "string",
      description: "Path to a CSV table, loaded via the injected loader.",
    },
  },
};

const MATCH_SOURCES_SCHEMA: JSONSchema = {
  type: "object",
  properties: {
    rows: { type: "array", items: { type: "object" } },
    file_path: { type: "string" },
    rows_b: { type: "array", items: { type: "object" } },
    file_path_b: { type: "string" },
  },
};

// ---------------------------------------------------------------------------
// Skill registry
// ---------------------------------------------------------------------------

export const AGENT_SKILLS: readonly SkillDef[] = [
  {
    id: "analyze_data",
    description:
      "Profile a dataset and recommend a matching strategy, with reasoning and alternatives.",
    inputSchema: ROWS_OR_PATH_SCHEMA,
    handler: async (args, ctx): Promise<SkillResult> => {
      const rows = await resolveTable(args, ctx);
      return ctx.session.analyze(rows) as unknown as SkillResult;
    },
  },
  {
    id: "auto_configure",
    description:
      "Run the iterative auto-config controller and return the committed config + telemetry.",
    inputSchema: ROWS_OR_PATH_SCHEMA,
    handler: async (args, ctx): Promise<SkillResult> => {
      const rows = await resolveTable(args, ctx);
      const out = await ctx.session.autoconfigure(rows);
      return { config: out.config, telemetry: out.telemetry };
    },
  },
  {
    id: "agent_deduplicate",
    description:
      "Deduplicate a dataset end-to-end: profile, strategy, gating, and review-queue accumulation.",
    inputSchema: ROWS_OR_PATH_SCHEMA,
    handler: async (args, ctx): Promise<SkillResult> => {
      const rows = await resolveTable(args, ctx);
      const out = await ctx.session.deduplicate(rows);
      return {
        results: out.results,
        reasoning: out.reasoning,
        confidence_distribution: out.confidence_distribution,
        storage: out.storage,
      };
    },
  },
  {
    id: "agent_match_sources",
    description:
      "Match two datasets (target vs reference) with profiling, strategy, and reasoning.",
    inputSchema: MATCH_SOURCES_SCHEMA,
    handler: async (args, ctx): Promise<SkillResult> => {
      const rowsA = await resolveTable(args, ctx, "rows", "file_path");
      const rowsB = await resolveTable(args, ctx, "rows_b", "file_path_b");
      const out = await ctx.session.matchSources(rowsA, rowsB);
      return { results: out.results, reasoning: out.reasoning };
    },
  },
  {
    id: "agent_compare_strategies",
    description:
      "Run multiple candidate strategies on the same dataset and compare proxy metrics.",
    inputSchema: ROWS_OR_PATH_SCHEMA,
    handler: async (args, ctx): Promise<SkillResult> => {
      const rows = await resolveTable(args, ctx);
      const gt = args.ground_truth as
        | readonly (readonly [number, number])[]
        | undefined;
      const out =
        gt !== undefined
          ? await ctx.session.compareStrategies(rows, gt)
          : await ctx.session.compareStrategies(rows);
      return { recommended: out.recommended, strategies: out.strategies };
    },
  },
  {
    id: "suggest_pprl",
    description:
      "Inspect column names for sensitive PII and recommend privacy-preserving record linkage.",
    inputSchema: ROWS_OR_PATH_SCHEMA,
    handler: async (args, ctx): Promise<SkillResult> => {
      const rows = await resolveTable(args, ctx);
      const profile = profileForAgent(rows);
      const decision = selectStrategy(profile);
      return {
        has_sensitive: profile.has_sensitive,
        strategy: decision.strategy,
        recommendation: profile.has_sensitive
          ? "Sensitive fields detected; use privacy-preserving record linkage (PPRL)."
          : "No sensitive fields detected; PPRL is available but not required.",
      };
    },
  },
];

// ---------------------------------------------------------------------------
// Dispatcher
// ---------------------------------------------------------------------------

const SKILLS_BY_ID: ReadonlyMap<string, SkillDef> = new Map(
  AGENT_SKILLS.map((s) => [s.id, s]),
);

/**
 * Run an agent skill by id. The handler resolves its table from inline rows or
 * the injected `loadTable`. On any throw — unknown id, missing loader, handler
 * error — returns `{ error: <message> }` (never throws).
 */
export async function dispatchSkill(
  id: string,
  args: Record<string, unknown>,
  ctx: SkillContext,
): Promise<SkillResult> {
  const def = SKILLS_BY_ID.get(id);
  if (def === undefined) {
    return { error: `Unknown skill: ${id}` };
  }
  try {
    return await def.handler(args, ctx);
  } catch (err) {
    return { error: String(err instanceof Error ? err.message : err) };
  }
}
