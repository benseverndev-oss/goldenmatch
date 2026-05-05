/**
 * mcp/memory-tools.ts -- Five MCP tools for Learning Memory.
 *
 * Mirrors goldenmatch/mcp/memory_tools.py. Each handler instantiates its own
 * SqliteMemoryStore, traps SQLite errors and returns structured TextContent
 * rather than crashing the JSON-RPC loop.
 *
 * Node-only: depends on SqliteMemoryStore (better-sqlite3 optional peer dep).
 */

import { SqliteMemoryStore } from "../memory/sqlite-store.js";
import { MemoryLearner } from "../../core/memory/learner.js";
import type {
  Correction,
  LearnedAdjustment,
  MemoryStore,
} from "../../core/memory/types.js";

// ---------------------------------------------------------------------------
// Tool type (matches the shape used in mcp/server.ts)
// ---------------------------------------------------------------------------

export interface Tool {
  readonly name: string;
  readonly description: string;
  readonly inputSchema: Readonly<Record<string, unknown>>;
}

export interface TextContent {
  readonly type: "text";
  readonly text: string;
}

const DEFAULT_PATH = ".goldenmatch/memory.db";

// ---------------------------------------------------------------------------
// Tool definitions (mirror Python memory_tools.py:19-126)
// ---------------------------------------------------------------------------

export const MEMORY_TOOLS: readonly Tool[] = [
  {
    name: "list_corrections",
    description:
      "List stored Learning Memory corrections, optionally filtered by " +
      "dataset. Returns id_a, id_b, decision, source, trust, reason, " +
      "matchkey_name, dataset, original_score, created_at.",
    inputSchema: {
      type: "object",
      properties: {
        dataset: {
          type: "string",
          description: "Optional dataset filter (e.g. file path).",
        },
        path: {
          type: "string",
          description:
            "SQLite memory DB path. Default: .goldenmatch/memory.db",
        },
      },
    },
  },
  {
    name: "add_correction",
    description:
      "Add a pair correction to Learning Memory. Source is set to 'agent' " +
      "with trust=0.5 (lower than human steward decisions which are 1.0). " +
      "Pair (id_a, id_b) is canonicalized to (min, max) before storage.",
    inputSchema: {
      type: "object",
      properties: {
        id_a: { type: "integer" },
        id_b: { type: "integer" },
        decision: {
          type: "string",
          enum: ["approve", "reject"],
        },
        dataset: {
          type: "string",
          description:
            "Dataset identifier (e.g. file path). Required, non-empty.",
        },
        reason: { type: "string" },
        matchkey_name: { type: "string" },
        path: {
          type: "string",
          description:
            "SQLite memory DB path. Default: .goldenmatch/memory.db",
        },
      },
      required: ["id_a", "id_b", "decision", "dataset"],
    },
  },
  {
    name: "learn_thresholds",
    description:
      "Force a MemoryLearner pass over accumulated corrections. Returns " +
      "the list of LearnedAdjustments produced (matchkey_name, threshold, " +
      "sample_size, learned_at). Requires >= 10 corrections per matchkey " +
      "before threshold tuning fires; otherwise returns an empty list.",
    inputSchema: {
      type: "object",
      properties: {
        matchkey_name: {
          type: "string",
          description: "Optional: learn only for this matchkey.",
        },
        path: {
          type: "string",
          description:
            "SQLite memory DB path. Default: .goldenmatch/memory.db",
        },
      },
    },
  },
  {
    name: "memory_stats",
    description:
      "Return Learning Memory status: total correction count, last learn " +
      "time, and current learned adjustments. Cheap; safe for status checks.",
    inputSchema: {
      type: "object",
      properties: {
        path: {
          type: "string",
          description:
            "SQLite memory DB path. Default: .goldenmatch/memory.db",
        },
      },
    },
  },
  {
    name: "memory_export",
    description:
      "Return all corrections as a list of dicts (CSV-shaped). Caller is " +
      "responsible for writing the file. Optionally filter by dataset.",
    inputSchema: {
      type: "object",
      properties: {
        dataset: { type: "string" },
        path: {
          type: "string",
          description:
            "SQLite memory DB path. Default: .goldenmatch/memory.db",
        },
      },
    },
  },
];

export const MEMORY_TOOL_NAMES: ReadonlySet<string> = new Set(
  MEMORY_TOOLS.map((t) => t.name),
);

// ---------------------------------------------------------------------------
// Serialization helpers (snake_case wire format, matches Python)
// ---------------------------------------------------------------------------

interface CorrectionDict {
  id: string;
  id_a: number;
  id_b: number;
  decision: string;
  source: string;
  trust: number;
  field_hash: string;
  record_hash: string;
  original_score: number;
  matchkey_name: string | null;
  reason: string | null;
  dataset: string | null;
  created_at: string | null;
}

function correctionToDict(c: Correction): CorrectionDict {
  return {
    id: c.id,
    id_a: c.idA,
    id_b: c.idB,
    decision: c.decision,
    source: c.source,
    trust: c.trust,
    field_hash: c.fieldHash,
    record_hash: c.recordHash,
    original_score: c.originalScore,
    matchkey_name: c.matchkeyName,
    reason: c.reason,
    dataset: c.dataset,
    created_at: c.createdAt ? c.createdAt.toISOString() : null,
  };
}

interface AdjustmentDict {
  matchkey_name: string;
  threshold: number | null;
  field_weights: Record<string, number> | null;
  sample_size: number;
  learned_at: string | null;
}

function adjustmentToDict(a: LearnedAdjustment): AdjustmentDict {
  return {
    matchkey_name: a.matchkeyName,
    threshold: a.threshold,
    field_weights: a.fieldWeights,
    sample_size: a.sampleSize,
    learned_at: a.learnedAt ? a.learnedAt.toISOString() : null,
  };
}

// ---------------------------------------------------------------------------
// Store factory (overridable in tests)
// ---------------------------------------------------------------------------

async function openStore(path: string): Promise<MemoryStore> {
  const store = new SqliteMemoryStore({
    enabled: true,
    backend: "sqlite",
    path,
    learning: { thresholdMinCorrections: 10, weightsMinCorrections: 50 },
  });
  await store.init();
  return store;
}

// ---------------------------------------------------------------------------
// Dispatcher
// ---------------------------------------------------------------------------

/**
 * Route a memory-tool MCP call to its handler. Returns JSON-encoded
 * TextContent. Unknown / SQLite errors are caught and returned as a
 * structured `{ error: ... }` payload rather than thrown.
 */
export async function handleMemoryTool(
  name: string,
  args: Record<string, unknown>,
): Promise<TextContent[]> {
  let result: Record<string, unknown>;
  try {
    result = await dispatch(name, args ?? {});
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    result = { error: msg };
  }
  return [
    {
      type: "text",
      text: JSON.stringify(result, null, 2),
    },
  ];
}

async function dispatch(
  name: string,
  args: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const path =
    typeof args["path"] === "string" && args["path"]
      ? (args["path"] as string)
      : DEFAULT_PATH;

  if (name === "list_corrections") {
    const dataset =
      typeof args["dataset"] === "string"
        ? (args["dataset"] as string)
        : undefined;
    const store = await openStore(path);
    let corrections: Correction[];
    try {
      corrections =
        dataset !== undefined
          ? await store.getCorrections({ dataset })
          : await store.getCorrections();
    } finally {
      await store.close?.();
    }
    return {
      count: corrections.length,
      corrections: corrections.map(correctionToDict),
    };
  }

  if (name === "add_correction") {
    const dataset = args["dataset"];
    if (typeof dataset !== "string" || dataset.length === 0) {
      return { error: "Missing or empty required parameter: dataset" };
    }
    const decision = args["decision"];
    if (decision !== "approve" && decision !== "reject") {
      return {
        error: `Invalid decision: ${JSON.stringify(decision)}. Use 'approve' or 'reject'.`,
      };
    }
    const idARaw = args["id_a"];
    const idBRaw = args["id_b"];
    const idA =
      typeof idARaw === "number" ? idARaw : parseInt(String(idARaw), 10);
    const idB =
      typeof idBRaw === "number" ? idBRaw : parseInt(String(idBRaw), 10);
    if (!Number.isFinite(idA) || !Number.isFinite(idB)) {
      return { error: "id_a / id_b must be integers" };
    }
    const ca = Math.min(idA, idB);
    const cb = Math.max(idA, idB);
    const id = crypto.randomUUID();
    const matchkeyName =
      typeof args["matchkey_name"] === "string"
        ? (args["matchkey_name"] as string)
        : null;
    const reason =
      typeof args["reason"] === "string" ? (args["reason"] as string) : null;
    const correction: Correction = {
      id,
      idA: ca,
      idB: cb,
      decision,
      source: "agent",
      trust: 0.5,
      fieldHash: "",
      recordHash: "",
      originalScore: 0.0,
      matchkeyName,
      reason,
      dataset,
      createdAt: new Date(),
    };
    const store = await openStore(path);
    try {
      await store.addCorrection(correction);
    } finally {
      await store.close?.();
    }
    return {
      status: "ok",
      id,
      id_a: ca,
      id_b: cb,
      decision,
      source: "agent",
      trust: 0.5,
      dataset,
    };
  }

  if (name === "learn_thresholds") {
    const matchkeyName =
      typeof args["matchkey_name"] === "string"
        ? (args["matchkey_name"] as string)
        : undefined;
    const store = await openStore(path);
    let adjustments: LearnedAdjustment[];
    try {
      const learner = new MemoryLearner(store);
      adjustments = await learner.learn(matchkeyName);
    } finally {
      await store.close?.();
    }
    return {
      count: adjustments.length,
      adjustments: adjustments.map(adjustmentToDict),
    };
  }

  if (name === "memory_stats") {
    const store = await openStore(path);
    try {
      const total = await store.countCorrections();
      const last = await store.lastLearnTime();
      const adjustments = await store.getAllAdjustments();
      return {
        total_corrections: total,
        last_learn_time: last ? last.toISOString() : null,
        adjustments: adjustments.map(adjustmentToDict),
      };
    } finally {
      await store.close?.();
    }
  }

  if (name === "memory_export") {
    const dataset =
      typeof args["dataset"] === "string"
        ? (args["dataset"] as string)
        : undefined;
    const store = await openStore(path);
    let corrections: Correction[];
    try {
      corrections =
        dataset !== undefined
          ? await store.getCorrections({ dataset })
          : await store.getCorrections();
    } finally {
      await store.close?.();
    }
    return {
      count: corrections.length,
      corrections: corrections.map(correctionToDict),
    };
  }

  return { error: `Unknown memory tool: ${name}` };
}
