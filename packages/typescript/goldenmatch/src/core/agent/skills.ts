/**
 * skills.ts — AGENT_SKILLS registry + dispatchSkill (the shared dispatcher).
 * Edge-safe: no Node.js imports. The I/O seam is dependency injection
 * (`ctx.loadTable`), never a file read here.
 *
 * Wave 1 registers only the AgentSession-backed skills. MCP/A2A wiring, file
 * loaders, and optional-dependency tools (scan_quality, run_transforms, ...)
 * land in Waves 2-4 — the registry is shaped so they slot in later.
 */

import type {
  Row,
  MatchkeyConfig,
  MatchkeyField,
  GoldenMatchConfig,
} from "../types.js";
import { makeMatchkeyConfig, makeMatchkeyField } from "../types.js";
import type { SkillDef, SkillContext, SkillResult, JSONSchema } from "./types.js";
import type { Correction } from "../memory/types.js";
import { profileForAgent, selectStrategy } from "./strategy.js";
import { explainPair } from "../explain.js";
import { gatePairs } from "../review-queue.js";
import { dedupe } from "../api.js";
import { reviewConfig, serializeSuggestions } from "../suggest.js";
import { autoConfigureRows } from "../autoconfig.js";

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
  // No inline rows and no path: still call loadTable so an injected loader can
  // raise the surface-specific "no loader" error (matches the test contract).
  return ctx.loadTable("");
}

/**
 * Coerce a tool argument to a finite integer, or null when it isn't one.
 * Mirrors the `add_correction` MCP tool's `id_a` / `id_b` parsing.
 */
function toInt(raw: unknown): number | null {
  const n = typeof raw === "number" ? raw : parseInt(String(raw), 10);
  return Number.isFinite(n) ? n : null;
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

const EXPLAIN_PAIR_SCHEMA: JSONSchema = {
  type: "object",
  properties: {
    record_a: { type: "object", description: "First record (column -> value)." },
    record_b: { type: "object", description: "Second record (column -> value)." },
    fuzzy: {
      type: "object",
      additionalProperties: { type: "number" },
      description: "Map of field -> weight scored with jaro_winkler.",
    },
    exact: {
      type: "array",
      items: { type: "string" },
      description: "Field names compared with the exact scorer.",
    },
  },
  required: ["record_a", "record_b"],
};

const EXPLAIN_CLUSTER_SCHEMA: JSONSchema = {
  type: "object",
  properties: {
    cluster_id: { type: "integer" },
  },
  required: ["cluster_id"],
};

const CONTROLLER_TELEMETRY_SCHEMA: JSONSchema = {
  type: "object",
  properties: {},
};

const APPROVE_REJECT_SCHEMA: JSONSchema = {
  type: "object",
  properties: {
    job_name: { type: "string" },
    id_a: { type: "integer" },
    id_b: { type: "integer" },
    decision: { type: "string", enum: ["approve", "reject"] },
    decided_by: { type: "string" },
    reason: { type: "string" },
  },
  required: ["id_a", "id_b", "decision"],
};

const SCAN_QUALITY_SCHEMA: JSONSchema = {
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
    domain: {
      type: "string",
      description: "Optional domain hint (healthcare, finance, ecommerce).",
    },
  },
};

const FIX_QUALITY_SCHEMA: JSONSchema = {
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
    fix_mode: {
      type: "string",
      enum: ["safe", "moderate"],
      description: "Fix aggressiveness: safe (conservative) or moderate. Default: safe.",
    },
    domain: {
      type: "string",
      description: "Optional domain hint (healthcare, finance, ecommerce).",
    },
  },
};

const REVIEW_CONFIG_SCHEMA: JSONSchema = {
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
    config: {
      type: "object",
      description:
        "Optional GoldenMatchConfig to review. Auto-configured from the data when omitted.",
    },
    verify: {
      type: "boolean",
      description:
        "Re-run the pipeline per candidate and drop health-worsening suggestions (default true).",
    },
  },
};

// ---------------------------------------------------------------------------
// agent_explain_pair: build a MatchkeyConfig from fuzzy/exact args
// ---------------------------------------------------------------------------

/**
 * Build the MatchkeyConfig that `explainPair` requires from the tool's
 * `fuzzy` (`{field: weight}`) and `exact` (`string[]`) args. Mirrors the
 * Python `explain_pair_df(record_a, record_b, fuzzy=, exact=)` shape.
 *
 * When neither is supplied, every shared key of the two records is compared
 * with `jaro_winkler` (a useful default for an ad-hoc pair explanation).
 */
function buildExplainMatchkey(
  rowA: Row,
  rowB: Row,
  fuzzy: unknown,
  exact: unknown,
): MatchkeyConfig {
  const fields: MatchkeyField[] = [];

  if (fuzzy !== null && typeof fuzzy === "object" && !Array.isArray(fuzzy)) {
    for (const [col, weight] of Object.entries(fuzzy as Record<string, unknown>)) {
      const w = typeof weight === "number" ? weight : Number(weight);
      fields.push(
        makeMatchkeyField({
          field: col,
          transforms: ["lowercase", "strip"],
          scorer: "jaro_winkler",
          weight: Number.isFinite(w) ? w : 1.0,
        }),
      );
    }
  }

  if (Array.isArray(exact)) {
    for (const col of exact) {
      fields.push(
        makeMatchkeyField({
          field: String(col),
          transforms: ["lowercase", "strip"],
          scorer: "exact",
        }),
      );
    }
  }

  if (fields.length === 0) {
    const shared = Object.keys(rowA).filter((k) => k in rowB);
    for (const col of shared) {
      fields.push(
        makeMatchkeyField({
          field: col,
          transforms: ["lowercase", "strip"],
          scorer: "jaro_winkler",
        }),
      );
    }
  }

  return makeMatchkeyConfig({
    name: "adhoc",
    type: "weighted",
    fields,
    threshold: 0.85,
  });
}

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
  {
    id: "agent_explain_pair",
    description: "Natural language explanation for a record pair.",
    inputSchema: EXPLAIN_PAIR_SCHEMA,
    handler: async (args): Promise<SkillResult> => {
      const rowA = args.record_a as Row | undefined;
      const rowB = args.record_b as Row | undefined;
      if (
        rowA === undefined ||
        rowB === undefined ||
        rowA === null ||
        rowB === null ||
        typeof rowA !== "object" ||
        typeof rowB !== "object"
      ) {
        return { error: "record_a and record_b are required objects" };
      }
      const mk = buildExplainMatchkey(rowA, rowB, args.fuzzy, args.exact);
      const explanation = explainPair(rowA, rowB, mk);
      return {
        score: explanation.score,
        confidence: explanation.confidence,
        explanation: explanation.explanation,
        field_scores: explanation.fieldScores,
        reasoning: explanation.reasoning,
      };
    },
  },
  {
    id: "agent_explain_cluster",
    description: "Explain why records are in the same cluster.",
    inputSchema: EXPLAIN_CLUSTER_SCHEMA,
    handler: async (args): Promise<SkillResult> => {
      const clusterId = args.cluster_id;
      return {
        cluster_id: clusterId !== undefined ? clusterId : null,
        note:
          "agent_explain_cluster requires a prior agent_deduplicate call. " +
          "Each dispatch is stateless; run agent_deduplicate first, then " +
          "inspect the clusters directly.",
      };
    },
  },
  {
    id: "controller_telemetry",
    description:
      "Return the AutoConfigController telemetry. Stateless dispatch cannot " +
      "read a prior run's telemetry; call auto_configure or agent_deduplicate " +
      "in the same invocation, which already returns telemetry inline.",
    inputSchema: CONTROLLER_TELEMETRY_SCHEMA,
    handler: async (): Promise<SkillResult> => {
      return {
        available: false,
        note:
          "controller_telemetry is per-session, but skill dispatch is " +
          "stateless. Call auto_configure or agent_deduplicate to get " +
          "telemetry alongside the result.",
      };
    },
  },
  {
    id: "agent_review_queue",
    description: "Get borderline pairs awaiting approval.",
    inputSchema: ROWS_OR_PATH_SCHEMA,
    handler: async (args, ctx): Promise<SkillResult> => {
      const rows = await resolveTable(args, ctx);
      const result = await dedupe(rows);
      const gated = gatePairs(result.scoredPairs);
      const pending = gated.needsReview.map((item) => ({
        id_a: item.idA,
        id_b: item.idB,
        score: item.score,
      }));
      return { pending, count: pending.length };
    },
  },
  {
    id: "agent_approve_reject",
    description: "Approve or reject a review queue pair.",
    inputSchema: APPROVE_REJECT_SCHEMA,
    handler: async (args, ctx): Promise<SkillResult> => {
      const decision = args.decision;
      if (decision !== "approve" && decision !== "reject") {
        return {
          error: `Invalid decision: ${String(decision)}. Use 'approve' or 'reject'.`,
        };
      }

      // Durable Learning Memory write, faithful to Python's
      // `_write_agent_correction`: source='agent', trust=0.5, empty
      // field/record hashes (staleness detection degrades gracefully),
      // original_score 0.0. The pair is canonicalized to (min, max) before
      // storage (the project-wide invariant, same as `add_correction`). When
      // no store is wired (edge path) the decision is still returned, matching
      // Python's `memory_store=None` branch.
      const idA = toInt(args.id_a);
      const idB = toInt(args.id_b);
      if (
        ctx.openMemoryStore !== undefined &&
        idA !== null &&
        idB !== null
      ) {
        const ca = Math.min(idA, idB);
        const cb = Math.max(idA, idB);
        const reason =
          typeof args.reason === "string" && args.reason.length > 0
            ? args.reason
            : null;
        const dataset =
          typeof args.dataset === "string" && args.dataset.length > 0
            ? args.dataset
            : ctx.dataset !== undefined
              ? ctx.dataset
              : null;
        const correction: Correction = {
          id: crypto.randomUUID(),
          idA: ca,
          idB: cb,
          decision,
          source: "agent",
          trust: 0.5,
          fieldHash: "",
          recordHash: "",
          originalScore: 0.0,
          matchkeyName: null,
          reason,
          dataset,
          createdAt: new Date(),
        };
        const store = await ctx.openMemoryStore();
        try {
          await store.addCorrection(correction);
        } finally {
          await store.close?.();
        }
      }

      // Response shape mirrors Python's `agent_approve_reject` handler:
      // {status, decision, job_name?, id_a, id_b, decided_by?}.
      const jobName =
        typeof args.job_name === "string" ? args.job_name : undefined;
      const decidedBy =
        typeof args.decided_by === "string" ? args.decided_by : undefined;
      return {
        status: "ok",
        decision,
        ...(jobName !== undefined ? { job_name: jobName } : {}),
        ...(args.id_a !== undefined ? { id_a: args.id_a } : {}),
        ...(args.id_b !== undefined ? { id_b: args.id_b } : {}),
        ...(decidedBy !== undefined ? { decided_by: decidedBy } : {}),
      };
    },
  },
  {
    id: "scan_quality",
    description:
      "Run GoldenCheck data quality scan on a dataset. Returns issues found " +
      "(encoding errors, Unicode problems, format violations) without applying " +
      "fixes. Requires goldencheck.",
    inputSchema: SCAN_QUALITY_SCHEMA,
    handler: async (args, ctx): Promise<SkillResult> => {
      try {
        await import("goldencheck" as string);
      } catch {
        return { error: "goldencheck not installed" };
      }
      // goldencheck present: still need the rows to scan.
      const rows = await resolveTable(args, ctx);
      return {
        error:
          "goldencheck integration is not wired in the TS core yet; " +
          `scanned 0 of ${rows.length} rows`,
      };
    },
  },
  {
    id: "fix_quality",
    description:
      "Run GoldenCheck scan and apply fixes to a dataset. Returns the fixed " +
      "data summary and a manifest of all fixes applied. Requires goldencheck.",
    inputSchema: FIX_QUALITY_SCHEMA,
    handler: async (args, ctx): Promise<SkillResult> => {
      try {
        await import("goldencheck" as string);
      } catch {
        return { error: "goldencheck not installed" };
      }
      const rows = await resolveTable(args, ctx);
      return {
        error:
          "goldencheck integration is not wired in the TS core yet; " +
          `fixed 0 of ${rows.length} rows`,
      };
    },
  },
  {
    id: "run_transforms",
    description:
      "Run GoldenFlow data transforms on a dataset. Normalizes phone numbers " +
      "(E.164), dates (ISO), categorical spelling, and Unicode issues. Returns " +
      "a manifest of transforms applied. Requires goldenflow.",
    inputSchema: ROWS_OR_PATH_SCHEMA,
    handler: async (args, ctx): Promise<SkillResult> => {
      try {
        await import("goldenflow" as string);
      } catch {
        return { error: "goldenflow not installed" };
      }
      const rows = await resolveTable(args, ctx);
      return {
        error:
          "goldenflow integration is not wired in the TS core yet; " +
          `transformed 0 of ${rows.length} rows`,
      };
    },
  },
  {
    id: "review_config",
    description:
      "Review a dedupe config and return verified config-improvement " +
      "suggestions (the healer). Opt-in: returns [] unless the suggest-wasm " +
      "backend is enabled.",
    inputSchema: REVIEW_CONFIG_SCHEMA,
    handler: async (args, ctx): Promise<SkillResult> => {
      const rows = await resolveTable(args, ctx);
      const explicit = args.config;
      const config: GoldenMatchConfig =
        explicit !== null && typeof explicit === "object" && !Array.isArray(explicit)
          ? (explicit as GoldenMatchConfig)
          : autoConfigureRows(rows);
      const verify = args.verify !== false; // default true (explicit request)
      const suggestions = await reviewConfig(rows, config, { verify });
      return {
        suggestions: serializeSuggestions(suggestions, { verified: verify }),
        count: suggestions.length,
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
