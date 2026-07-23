/**
 * types.ts — Agent decision-core types.
 * Edge-safe: no Node.js imports. Ported from goldenmatch/core/agent.py.
 */

import type { Row } from "../types.js";

// ---------------------------------------------------------------------------
// Data profiles (mirror Python FieldProfile / DataProfile)
// ---------------------------------------------------------------------------

export interface FieldProfile {
  /** Column name. */
  readonly name: string;
  /** "string" | "numeric" | "other". */
  readonly type: "string" | "numeric" | "other";
  /** n_unique / row_count, 0-1. */
  readonly uniqueness: number;
  /** fraction null, 0-1. */
  readonly null_rate: number;
  /** mean string byte length (0 for non-string). */
  readonly avg_length: number;
}

export interface DataProfile {
  readonly row_count: number;
  readonly fields: FieldProfile[];
  readonly has_sensitive: boolean;
}

// ---------------------------------------------------------------------------
// Strategy decision (mirror Python StrategyDecision)
// ---------------------------------------------------------------------------

export interface StrategyDecision {
  readonly strategy: string;
  readonly why: string;
  readonly domain: string | null;
  readonly strong_ids: string[];
  readonly fuzzy_fields: string[];
  readonly backend: string | null;
  readonly auto_execute: boolean;
}

export type Alternative = { strategy: string; why_not: string };

// ---------------------------------------------------------------------------
// Telemetry — minimal cross-surface shape (mirrors Python's _capture_telemetry
// fallback: {available, source, stop_reason?}).
// ---------------------------------------------------------------------------

export interface Telemetry {
  readonly available: boolean;
  readonly source: string;
  readonly stop_reason?: string;
  readonly health?: string;
}

// ---------------------------------------------------------------------------
// Analyze / autoconfigure / deduplicate result shapes
// ---------------------------------------------------------------------------

/** A single rounded field entry in the analyze() reasoning payload. */
export interface AnalyzeFieldProfile {
  readonly name: string;
  readonly type: string;
  readonly uniqueness: number;
  readonly null_rate: number;
  readonly avg_length: number;
}

export interface AnalyzeResult {
  readonly profile: {
    readonly row_count: number;
    readonly fields: AnalyzeFieldProfile[];
    readonly has_sensitive: boolean;
  };
  readonly strategy: string;
  readonly why: string;
  readonly domain: string | null;
  readonly strong_ids: string[];
  readonly fuzzy_fields: string[];
  readonly backend: string | null;
  readonly auto_execute: boolean;
  readonly alternatives: Alternative[];
}

export interface ConfidenceDistribution {
  readonly auto_merged: number;
  readonly review: number;
  readonly auto_rejected: number;
  readonly total_pairs: number;
}

export interface AutoconfigResult {
  readonly config: import("../types.js").GoldenMatchConfig;
  readonly telemetry: Telemetry;
}

export interface DeduplicateResult {
  readonly results: import("../types.js").DedupeResult;
  readonly reasoning: Record<string, unknown>;
  readonly confidence_distribution: ConfidenceDistribution;
  /** Review-queue storage tier. Always "memory" in the edge-safe core. */
  readonly storage: string;
}

export interface MatchSourcesResult {
  readonly results: import("../types.js").MatchResult;
  readonly reasoning: Record<string, unknown>;
}

/** Per-strategy proxy metrics in a compareStrategies result. */
export interface StrategyMetrics {
  readonly clusters?: number;
  readonly match_rate?: number;
  readonly total_pairs?: number;
  readonly precision?: number;
  readonly recall?: number;
  readonly f1?: number;
  readonly error?: string;
}

export interface CompareResult {
  readonly recommended: string;
  readonly strategies: Record<string, StrategyMetrics>;
}

// ---------------------------------------------------------------------------
// Skill registry + I/O seam types
// ---------------------------------------------------------------------------

/** Minimal JSON-schema shape carried by each skill. Reused by MCP/A2A. */
export type JSONSchema = Readonly<Record<string, unknown>>;

/** Anything an agent skill handler may return (incl. {error: ...}). */
export type SkillResult = Record<string, unknown>;

export interface SkillDef {
  readonly id: string;
  readonly description: string;
  readonly inputSchema: JSONSchema;
  readonly handler: (
    args: Record<string, unknown>,
    ctx: SkillContext,
  ) => Promise<SkillResult>;
}

/**
 * The dependency-injected context handed to every skill handler. The I/O seam
 * is `loadTable`: core never reads files; node surfaces inject a CSV loader.
 * `session` is fresh per call (statelessness, matching Python).
 */
export interface SkillContext {
  // `AgentSession` is imported lazily by consumers to avoid a types->session
  // circular import; typed as `unknown`-bearing here keeps types.ts leaf.
  readonly session: import("./session.js").AgentSession;
  loadTable(source: string): Promise<Row[]>;
  /**
   * Optional durable Learning Memory store factory. The node surfaces
   * (MCP / A2A) inject it; the edge path leaves it absent. When present,
   * `agent_approve_reject` persists an agent correction (source='agent',
   * trust=0.5), mirroring Python's `_write_agent_correction`. When absent the
   * decision is still returned but not persisted — matching Python's
   * `memory_store=None` branch. The handler opens the store, writes, and
   * closes it, mirroring the `add_correction` MCP tool's lifecycle.
   */
  openMemoryStore?: () => Promise<import("../memory/types.js").MemoryStore>;
  /**
   * Optional dataset identifier threaded into a persisted correction's
   * `dataset` field. Absent (→ null) on the stateless MCP path, matching
   * Python's MCP dispatch (which passes `dataset=None`).
   */
  readonly dataset?: string;
}
