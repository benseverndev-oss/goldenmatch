#!/usr/bin/env node
/**
 * mcp/server.ts -- GoldenMatch MCP server (stdio transport, JSON-RPC).
 *
 * Node-only: uses node:fs, node:path, node:readline. NOT edge-safe.
 *
 * Exposes 83 tools covering dedupe, match, scoring, explanation,
 * profiling, auto-config (shorthand), evaluation, listings, the Splink ->
 * GoldenMatch config converter (convert_splink_config), CCMS cluster
 * comparison (compare_clusters), Learning Memory (6 memory tools via
 * MEMORY_TOOLS, incl. memory_import), the Identity Graph (8 identity tools
 * via IDENTITY_TOOLS, incl. identity_claim + identity_resolve_conflict),
 * the AgentSession skills (15 agent tools via AGENT_MCP_TOOLS, incl. the
 * healer's review_config), the stateful run tools (7 via RUN_TOOLS:
 * get_stats/list_clusters/get_cluster/get_golden_record/export_results/
 * upload_dataset/lineage, backed by the server-held RUN_STORE), the rollback
 * subsystem tools (list_runs/rollback via ROLLBACK_TOOLS, backed by the
 * on-disk .goldenmatch_runs.json run log), the cluster-surgery tools
 * (unmerge_record/shatter_cluster via SURGERY_TOOLS, which mutate the current
 * RUN_STORE run in place), and the user-defined domain rulebooks
 * (list_domains/create_domain/test_domain via DOMAIN_TOOLS, over YAML in
 * .goldenmatch/domains).
 *
 * Every tool dispatch is wrapped in try/catch so a single failure never
 * crashes the JSON-RPC loop; errors come back as `{ error: "<msg>" }`.
 *
 * Ports ideas from goldenmatch/mcp/server.py.
 */

import { readFileSync } from "node:fs";
import { isAbsolute } from "node:path";
import { createInterface } from "node:readline";

import { dedupe, match, scoreStrings } from "../../core/api.js";
import { readFile, writeCsv, writeJson } from "../connectors/file.js";
import { loadConfigFile } from "../config-file.js";
import { autoMapColumns } from "../../core/schema-match.js";
import { runPPRL, type PPRLConfig } from "../../core/pprl/protocol.js";
import { diagnoseConfig } from "../../core/config-critique.js";
import type { Row, MatchkeyField } from "../../core/types.js";
import {
  makeMatchkeyConfig,
  makeMatchkeyField,
  VALID_SCORERS,
  VALID_TRANSFORMS,
  VALID_STRATEGIES,
} from "../../core/types.js";
import {
  scoreField,
  findExactMatches,
  findFuzzyMatches,
  scorePair,
} from "../../core/scorer.js";
import { addRowIds } from "../../core/matchkey.js";
import { buildClusters } from "../../core/cluster.js";
import { compareClusters, ccmsSummary, parseClustersJson } from "../../core/compare-clusters.js";
import { analyzeBlocking } from "../../core/block-analyzer.js";
import {
  certifyRecallRows,
  toCertifyRecallResponse,
} from "../../core/recall-certificate.js";
import { runIncremental } from "../../core/incremental.js";
import {
  retrieveSimilar,
  retrievedRecordToDict,
} from "../../core/retrieve-similar.js";
import { getEmbedder } from "../../core/embedder.js";
import {
  runSensitivitySweep,
  sweepStabilityReport,
} from "../../core/sensitivity.js";
import type { SweepSpec } from "../../core/sensitivity.js";
import { autoConfigureRows } from "../../core/autoconfig.js";
import { getMatchkeys } from "../../core/types.js";
import { sanitizePath } from "./paths.js";
import { RUN_STORE } from "./run-store.js";
import { RUN_TOOLS, RUN_TOOL_NAMES, handleRunTool } from "./run-tools.js";
import { DOMAIN_TOOLS, DOMAIN_TOOL_NAMES, handleDomainTool } from "./domain-tools.js";
import {
  ROLLBACK_TOOLS,
  ROLLBACK_TOOL_NAMES,
  handleRollbackTool,
} from "./rollback-tools.js";
import {
  SURGERY_TOOLS,
  SURGERY_TOOL_NAMES,
  handleSurgeryTool,
} from "./surgery-tools.js";
import { explainPair, explainCluster } from "../../core/explain.js";
import { profileRows } from "../../core/profiler.js";
import { evaluatePairs, loadGroundTruthPairs } from "../../core/evaluate.js";
import { fromSplink, SplinkConversionError } from "../../core/config/from-splink.js";
import { emResultToJson } from "../../core/probabilistic.js";
import { stringifyConfigYaml } from "../config-file.js";
import {
  MEMORY_TOOLS,
  MEMORY_TOOL_NAMES,
  handleMemoryTool,
} from "./memory-tools.js";
import {
  IDENTITY_TOOLS,
  IDENTITY_TOOL_NAMES,
  handleIdentityTool,
} from "./identity-tools.js";
import {
  AGENT_MCP_TOOLS,
  AGENT_TOOL_NAMES,
  handleAgentTool,
} from "./agent-tools.js";

// ---------------------------------------------------------------------------
// Tool definitions
// ---------------------------------------------------------------------------

interface Tool {
  readonly name: string;
  readonly description: string;
  readonly inputSchema: Readonly<Record<string, unknown>>;
}

const pathArg = { type: "string", description: "File path (csv/tsv/json/jsonl)" };
const optionalConfigArg = {
  type: "string",
  description: "Optional path to YAML config file",
};
const optionalFieldsArg = {
  type: "array",
  items: { type: "string" },
  description: "Column names",
};
const stringArg = { type: "string" };
const rowArg = {
  type: "object",
  additionalProperties: true,
  description: "Record object (column -> value)",
};

const EXISTING_TOOLS: readonly Tool[] = [
  {
    name: "dedupe",
    description:
      "Deduplicate records in a file. Returns cluster counts and optional output path.",
    inputSchema: {
      type: "object",
      properties: {
        path: pathArg,
        config: optionalConfigArg,
        exact: optionalFieldsArg,
        fuzzy: {
          type: "object",
          additionalProperties: { type: "number" },
          description: "Map of field -> fuzzy threshold",
        },
        blocking: optionalFieldsArg,
        threshold: { type: "number", description: "Overall fuzzy threshold" },
        output: { type: "string", description: "Optional output path for golden records" },
      },
      required: ["path"],
    },
  },
  {
    name: "match",
    description:
      "Match a target file against a reference file. Returns matched/unmatched counts.",
    inputSchema: {
      type: "object",
      properties: {
        target: pathArg,
        reference: pathArg,
        config: optionalConfigArg,
        exact: optionalFieldsArg,
        fuzzy: {
          type: "object",
          additionalProperties: { type: "number" },
        },
        blocking: optionalFieldsArg,
        threshold: { type: "number" },
        output: { type: "string" },
      },
      required: ["target", "reference"],
    },
  },
  {
    name: "score_strings",
    description:
      "Score similarity between two strings using the requested scorer.",
    inputSchema: {
      type: "object",
      properties: {
        a: stringArg,
        b: stringArg,
        scorer: {
          type: "string",
          description:
            "Scorer name (exact, jaro_winkler, levenshtein, token_sort, soundex_match, dice, jaccard, ensemble)",
        },
      },
      required: ["a", "b"],
    },
  },
  {
    name: "score_pair",
    description:
      "Score two record objects across weighted fields. Returns a combined score.",
    inputSchema: {
      type: "object",
      properties: {
        row_a: rowArg,
        row_b: rowArg,
        fields: {
          type: "array",
          items: {
            type: "object",
            properties: {
              field: { type: "string" },
              scorer: { type: "string" },
              weight: { type: "number" },
              transforms: { type: "array", items: { type: "string" } },
            },
            required: ["field"],
          },
        },
      },
      required: ["row_a", "row_b", "fields"],
    },
  },
  {
    name: "explain_pair",
    description:
      "Explain why two records match (or don't) using a matchkey definition.",
    inputSchema: {
      type: "object",
      properties: {
        row_a: rowArg,
        row_b: rowArg,
        fields: {
          type: "array",
          items: {
            type: "object",
            properties: {
              field: { type: "string" },
              scorer: { type: "string" },
              weight: { type: "number" },
              transforms: { type: "array", items: { type: "string" } },
            },
            required: ["field"],
          },
        },
        threshold: { type: "number" },
      },
      required: ["row_a", "row_b", "fields"],
    },
  },
  {
    name: "explain_cluster",
    description:
      "Run dedupe on a file and explain the cluster containing the given row id.",
    inputSchema: {
      type: "object",
      properties: {
        path: pathArg,
        config: optionalConfigArg,
        exact: optionalFieldsArg,
        fuzzy: {
          type: "object",
          additionalProperties: { type: "number" },
        },
        blocking: optionalFieldsArg,
        row_id: { type: "number" },
      },
      required: ["path", "row_id"],
    },
  },
  {
    name: "profile",
    description:
      "Profile a dataset: per-column null rate, cardinality, inferred type, samples.",
    inputSchema: {
      type: "object",
      properties: { path: pathArg },
      required: ["path"],
    },
  },
  {
    name: "suggest_config",
    description:
      "Suggest a shorthand dedupe config based on a profile of the dataset.",
    inputSchema: {
      type: "object",
      properties: { path: pathArg },
      required: ["path"],
    },
  },
  {
    name: "evaluate",
    description:
      "Evaluate predicted pairs from a dedupe run against ground truth pairs.",
    inputSchema: {
      type: "object",
      properties: {
        path: pathArg,
        ground_truth: pathArg,
        id_col_a: { type: "string", description: "Ground truth id column A (default id_a)" },
        id_col_b: { type: "string", description: "Ground truth id column B (default id_b)" },
        config: optionalConfigArg,
        exact: optionalFieldsArg,
        fuzzy: {
          type: "object",
          additionalProperties: { type: "number" },
        },
        blocking: optionalFieldsArg,
        threshold: { type: "number" },
      },
      required: ["path", "ground_truth"],
    },
  },
  {
    name: "find_exact_matches",
    description: "Find exact matches on a field in a file. Returns pairs.",
    inputSchema: {
      type: "object",
      properties: {
        path: pathArg,
        field: { type: "string" },
        transforms: {
          type: "array",
          items: { type: "string" },
          description: "Transforms applied before matching (default lowercase, strip)",
        },
      },
      required: ["path", "field"],
    },
  },
  {
    name: "find_fuzzy_matches",
    description: "Find fuzzy matches in a block of rows. Returns scored pairs.",
    inputSchema: {
      type: "object",
      properties: {
        path: pathArg,
        field: { type: "string" },
        scorer: { type: "string", description: "Scorer (default jaro_winkler)" },
        threshold: { type: "number", description: "Threshold (default 0.85)" },
        transforms: { type: "array", items: { type: "string" } },
      },
      required: ["path", "field"],
    },
  },
  {
    name: "build_clusters",
    description:
      "Group records into clusters given a file and matchkey definition.",
    inputSchema: {
      type: "object",
      properties: {
        path: pathArg,
        exact: optionalFieldsArg,
        fuzzy: {
          type: "object",
          additionalProperties: { type: "number" },
        },
        blocking: optionalFieldsArg,
        threshold: { type: "number" },
      },
      required: ["path"],
    },
  },
  {
    name: "list_scorers",
    description: "List all available similarity scorers.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "list_transforms",
    description: "List all available field transforms.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "list_strategies",
    description: "List all golden-record survivorship strategies.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "list_blocking_strategies",
    description: "List all blocking strategy names.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "server_info",
    description: "Return metadata about this GoldenMatch MCP server.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "read_file",
    description: "Read a CSV/JSON file and return the first N records.",
    inputSchema: {
      type: "object",
      properties: {
        path: pathArg,
        limit: { type: "number", description: "Max rows to return (default 100)" },
      },
      required: ["path"],
    },
  },
  {
    name: "write_csv",
    description: "Write a list of record objects to a CSV file.",
    inputSchema: {
      type: "object",
      properties: {
        path: pathArg,
        rows: { type: "array", items: { type: "object", additionalProperties: true } },
      },
      required: ["path", "rows"],
    },
  },
  {
    name: "convert_splink_config",
    description:
      "Convert a Splink settings JSON (bare or trained) into a GoldenMatch " +
      "config. Pass the settings as an inline JSON string -- no filesystem " +
      "access needed. Returns the config as YAML, a findings report " +
      "(severity/splink_path/message/mapped_to per finding), a summary, and " +
      "-- when the Splink input carried trained m/u probabilities -- the " +
      "imported EM model as a dict you can persist yourself. strict=True " +
      "fails on ANY lossy mapping (not just hard errors).",
    inputSchema: {
      type: "object",
      properties: {
        settings_json: {
          type: "string",
          description: "Splink settings (bare or trained model) as a JSON string",
        },
        strict: {
          type: "boolean",
          description: "Fail on any lossy mapping (warnings), not just errors",
        },
      },
      required: ["settings_json"],
    },
  },
  {
    name: "compare_clusters",
    description:
      "Compare two ER clustering outcomes on the same rows via CCMS (Case " +
      "Count Metric System). Pass two clusters-JSON file paths (baseline ER1 " +
      "and comparison ER2); each file is a mapping of cluster id -> members " +
      "(a `{\"members\": [...]}` object or a bare list), optionally under a " +
      "`{\"clusters\": {...}}` wrapper. Returns per-case counts " +
      "(unchanged/merged/partitioned/overlapping) with percentages, record " +
      "and cluster counts (rc/cc1/cc2), singleton counts (sc1/sc2), and the " +
      "Talburt-Wang Index (twi). Stateless -- needs no loaded dataset.",
    inputSchema: {
      type: "object",
      properties: {
        clusters_a_path: { type: "string", description: "Path to the baseline (ER1) clusters JSON" },
        clusters_b_path: { type: "string", description: "Path to the comparison (ER2) clusters JSON" },
      },
      required: ["clusters_a_path", "clusters_b_path"],
    },
  },
  {
    name: "schema_match",
    description:
      "Auto-map columns between two files with different schemas. Returns " +
      "proposed (col_a, col_b) mappings with a confidence score and method " +
      "(exact_name / synonym / name_sim / partial_name / value_overlap / " +
      "composite). Stateless -- needs no loaded dataset; useful before matching " +
      "two sources.",
    inputSchema: {
      type: "object",
      properties: {
        file_a: { type: "string", description: "Path to source A (csv/tsv/json/jsonl)" },
        file_b: { type: "string", description: "Path to source B (csv/tsv/json/jsonl)" },
        min_score: { type: "number", description: "Minimum mapping score to keep (default 0.5)" },
      },
      required: ["file_a", "file_b"],
    },
  },
  {
    name: "pprl_link",
    description:
      "Run privacy-preserving record linkage (PPRL) between two parties' CSV " +
      "files. Encodes each party's fields as Bloom-filter CLKs and matches " +
      "records without sharing raw values. Stateless -- reads the two files " +
      "directly. Specify the shared `fields`, a `threshold`, and a " +
      "`security_level` (standard/high/paranoid picks the ngram/hash/size).",
    inputSchema: {
      type: "object",
      properties: {
        file_a: { type: "string", description: "Path to party A's CSV file" },
        file_b: { type: "string", description: "Path to party B's CSV file" },
        fields: {
          type: "array",
          items: { type: "string" },
          description: "Shared field names to link on (present in both files)",
        },
        threshold: { type: "number", description: "Match threshold (default 0.85)" },
        security_level: {
          type: "string",
          enum: ["standard", "high", "paranoid"],
          description: "Bloom params: standard=2/20/512, high=2/30/1024, paranoid=3/40/2048 (default high)",
        },
      },
      required: ["file_a", "file_b", "fields"],
    },
  },
  {
    name: "config_weaknesses",
    description:
      "Diagnose weaknesses in the current run's auto-config: columns admitted " +
      "that shouldn't be (source/provenance labels, per-row IDs), oversized or " +
      "shared-value blocks, null sinks, low-signal matchkeys, and over-merging. " +
      "Returns ranked findings, each with a plain-English explanation + a " +
      "concrete fix, plus a one-paragraph summary. Reads the current run (the " +
      "last dedupe in this session).",
    inputSchema: {
      type: "object",
      properties: {
        max_findings: {
          type: "integer",
          description: "Max findings to return, ranked by severity (default 6).",
        },
        phrasing: {
          type: "string",
          enum: ["plain", "technical"],
          description: "Wording style for the findings (default plain).",
        },
      },
    },
  },
  {
    name: "analyze_blocking",
    description:
      "Diagnose blocking on the loaded dataset: returns ranked blocking " +
      "key candidates with block counts, max block size, total candidate " +
      "comparisons, and estimated recall. Use it to explain why matching " +
      "is slow or produces too many candidate pairs. Reads the current run " +
      "(the last dedupe in this session).",
    inputSchema: {
      type: "object",
      properties: {
        sample_size: { type: "integer", description: "Recall-estimation sample size (default 1000)" },
        target_block_size: { type: "integer", description: "Target block size for scoring (default 5000)" },
        limit: { type: "integer", description: "Top N suggestions (default 10)" },
      },
    },
  },
  {
    name: "certify_recall",
    description:
      "Estimate match RECALL without ground truth (unsupervised). Treats " +
      "each auto-configured matchkey/pass as a decorrelated system and uses " +
      "capture-recapture over their overlaps to estimate how many true " +
      "matches were missed. Returns a point estimate (a safe lower bound " +
      "additionally needs a small labelled audit; see `goldenmatch evaluate " +
      "--certify --audit-out`). Needs >=3 decorrelated systems.",
    inputSchema: {
      type: "object",
      properties: {
        file_path: { type: "string", description: "Dataset to dedupe + certify" },
      },
      required: ["file_path"],
    },
  },
  {
    name: "sensitivity",
    description:
      "Parameter-sensitivity analysis: sweep one or more config " +
      "parameters across a range and report how stable the clustering " +
      "is at each value (CCMS unchanged %). Use it to find robust " +
      "thresholds. Auto-configures the file if no config is given.",
    inputSchema: {
      type: "object",
      properties: {
        file_path: { type: "string", description: "CSV/Parquet to analyze" },
        sweep: {
          type: "array",
          items: { type: "string" },
          description:
            "Sweep specs as 'field:start:stop:step', e.g. " +
            "'threshold:0.70:0.95:0.05'. One or more.",
        },
        config: { type: "string", description: "Optional config YAML path" },
        sample_size: {
          type: "integer",
          description: "Optional: sample N records before sweeping",
        },
      },
      required: ["file_path", "sweep"],
    },
  },
  {
    name: "incremental",
    description:
      "Match a batch of new records against an existing base dataset " +
      "(without re-running the whole base). Returns matched " +
      "(new_row_id, base_row_id, score) pairs plus counts. " +
      "Auto-configures from the base file if no config is given.",
    inputSchema: {
      type: "object",
      properties: {
        base_file: { type: "string", description: "Existing base dataset path" },
        new_records: { type: "string", description: "New records file to match in" },
        config: { type: "string", description: "Optional config YAML path" },
        threshold: { type: "number", description: "Optional threshold override" },
      },
      required: ["base_file", "new_records"],
    },
  },
  {
    name: "retrieve_similar",
    description:
      "Semantic retrieval (#1089): return the records in a CSV most similar " +
      "to a free-text query, ranked by cosine similarity. Embeds the chosen " +
      "column and the query, then runs ANN search. The read side of the RAG " +
      "entity-canonicalization epic -- fetch candidate records by query " +
      "without running a full dedupe. EMBEDDER IS CALLER-SUPPLIED: unlike the " +
      "Python tool (which defaults to a bundled zero-config in-house model), " +
      "this TS surface carries only the embedding kernel, not a model, so you " +
      "MUST pass an embedder `provider` (openai/vertex/voyage) plus its " +
      "credentials; it errors clearly if none is given.",
    inputSchema: {
      type: "object",
      properties: {
        file_path: { type: "string", description: "CSV/TSV/JSON corpus to search" },
        query: { type: "string", description: "Free-text query to search for" },
        column: { type: "string", description: "Column of the corpus to embed + search" },
        k: { type: "integer", description: "Max records to return (default 20)" },
        threshold: {
          type: "number",
          description: "Minimum cosine similarity in [-1, 1] (default 0.0)",
        },
        provider: {
          type: "string",
          enum: ["openai", "vertex", "voyage"],
          description:
            "REQUIRED embedder provider (no bundled model on the TS surface). " +
            "Credentials come from api_key or the provider's env var.",
        },
        model: {
          type: "string",
          description: "Embedder model id (defaults to the provider's default model).",
        },
        api_key: {
          type: "string",
          description: "Embedder API key (else read from the provider's env var).",
        },
        filters: {
          type: "object",
          additionalProperties: true,
          description: "Optional {column: value} equality pre-filter applied before embedding",
        },
      },
      required: ["file_path", "query", "column", "provider"],
    },
  },
];

// Cross-language naming aliases (Python<->TS MCP parity). Each forwards to an
// existing handler via a fall-through switch case below; schemas are derived
// from the canonical tool so they can't drift.
const _TS_TOOL_ALIASES: Record<string, string> = {
  find_duplicates: "dedupe",
  match_record: "match",
  explain_match: "explain_pair",
  profile_data: "profile",
};

const ALIAS_TOOLS: Tool[] = Object.entries(_TS_TOOL_ALIASES).map(([alias, canonical]) => {
  const c = EXISTING_TOOLS.find((t) => t.name === canonical);
  if (!c) throw new Error(`alias canonical not found: ${canonical}`);
  return { ...c, name: alias, description: `Alias for \`${canonical}\`. ${c.description}` };
});

export const TOOLS: readonly Tool[] = [
  ...EXISTING_TOOLS,
  ...ALIAS_TOOLS,
  ...MEMORY_TOOLS,
  ...IDENTITY_TOOLS,
  ...AGENT_MCP_TOOLS,
  ...RUN_TOOLS,
  ...ROLLBACK_TOOLS,
  ...SURGERY_TOOLS,
  ...DOMAIN_TOOLS,
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
//
// `sanitizePath` now lives in ./paths.ts (shared with run-tools.ts).

function asStringArray(v: unknown): string[] | undefined {
  if (v === undefined || v === null) return undefined;
  if (!Array.isArray(v)) return undefined;
  return v.map((x) => String(x));
}

function asNumberMap(v: unknown): Record<string, number> | undefined {
  if (v === undefined || v === null) return undefined;
  if (typeof v !== "object" || Array.isArray(v)) return undefined;
  const out: Record<string, number> = {};
  for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
    const n = typeof val === "number" ? val : Number(val);
    if (Number.isFinite(n)) out[k] = n;
  }
  return out;
}

interface ShorthandArgs {
  exact?: readonly string[];
  fuzzy?: Readonly<Record<string, number>>;
  blocking?: readonly string[];
  threshold?: number;
  configPath?: string;
}

function buildDedupeOptions(args: Record<string, unknown>): {
  config?: ReturnType<typeof loadConfigFile>;
  exact?: readonly string[];
  fuzzy?: Readonly<Record<string, number>>;
  blocking?: readonly string[];
  threshold?: number;
} {
  const opts: {
    config?: ReturnType<typeof loadConfigFile>;
    exact?: readonly string[];
    fuzzy?: Readonly<Record<string, number>>;
    blocking?: readonly string[];
    threshold?: number;
  } = {};

  if (typeof args["config"] === "string" && args["config"]) {
    opts.config = loadConfigFile(sanitizePath(args["config"] as string));
  }
  const exact = asStringArray(args["exact"]);
  if (exact) opts.exact = exact;
  const fuzzy = asNumberMap(args["fuzzy"]);
  if (fuzzy) opts.fuzzy = fuzzy;
  const blocking = asStringArray(args["blocking"]);
  if (blocking) opts.blocking = blocking;
  if (typeof args["threshold"] === "number") opts.threshold = args["threshold"];

  return opts;
}

function buildFieldsFromArg(raw: unknown): MatchkeyField[] {
  if (!Array.isArray(raw)) {
    throw new Error("fields must be an array of field configs");
  }
  const out: MatchkeyField[] = [];
  for (const entry of raw) {
    if (entry === null || typeof entry !== "object") continue;
    const e = entry as Record<string, unknown>;
    if (typeof e["field"] !== "string") {
      throw new Error("each field entry needs a string 'field' property");
    }
    const transforms = asStringArray(e["transforms"]) ?? ["lowercase", "strip"];
    const scorer = typeof e["scorer"] === "string" ? (e["scorer"] as string) : "jaro_winkler";
    const weight = typeof e["weight"] === "number" ? (e["weight"] as number) : 1.0;
    out.push(
      makeMatchkeyField({
        field: e["field"] as string,
        transforms,
        scorer,
        weight,
      }),
    );
  }
  return out;
}

// ---------------------------------------------------------------------------
// Tool dispatch
// ---------------------------------------------------------------------------

export async function handleTool(
  name: string,
  rawArgs: Record<string, unknown>,
): Promise<unknown> {
  const args = rawArgs ?? {};
  try {
    if (MEMORY_TOOL_NAMES.has(name)) {
      const content = await handleMemoryTool(name, args);
      // The MCP server wraps non-memory results in JSON.stringify themselves;
      // memory tools already return TextContent. Parse the JSON back so the
      // outer dispatch shape stays consistent with the other tools.
      const text = content[0]?.text ?? "{}";
      try {
        return JSON.parse(text);
      } catch {
        return { error: "memory tool returned non-JSON" };
      }
    }
    if (IDENTITY_TOOL_NAMES.has(name)) {
      const content = await handleIdentityTool(name, args);
      const text = content[0]?.text ?? "{}";
      try {
        return JSON.parse(text);
      } catch {
        return { error: "identity tool returned non-JSON" };
      }
    }
    // Agent skills return a plain object (like the core switch handlers below),
    // so no TextContent unwrap is needed -- the tools/call response wrap applies.
    if (AGENT_TOOL_NAMES.has(name)) {
      return await handleAgentTool(name, args);
    }
    // Run tools read the current run from RUN_STORE (populated by dedupe below).
    if (RUN_TOOL_NAMES.has(name)) {
      return handleRunTool(name, args);
    }
    // Rollback subsystem tools operate on the on-disk .goldenmatch_runs.json log.
    if (ROLLBACK_TOOL_NAMES.has(name)) {
      return handleRollbackTool(name, args);
    }
    // Cluster-surgery tools mutate the current run in RUN_STORE in place.
    if (SURGERY_TOOL_NAMES.has(name)) {
      return await handleSurgeryTool(name, args);
    }
    // Domain-rulebook tools read/write user YAML under .goldenmatch/domains.
    if (DOMAIN_TOOL_NAMES.has(name)) {
      return handleDomainTool(name, args);
    }
    switch (name) {
      case "find_duplicates":   // alias
      case "dedupe": {
        const path = sanitizePath(String(args["path"]));
        const rows = readFile(path);
        const options = buildDedupeOptions(args);
        const result = await dedupe(rows, options);
        // Persist the run so the stateful query tools (get_stats / list_clusters
        // / get_cluster / get_golden_record / export_results) can read it.
        const rowsById = new Map<number, Row>();
        for (const r of addRowIds(rows)) {
          rowsById.set(r["__row_id__"] as number, r);
        }
        const run_id = RUN_STORE.put({ result, rowsById, sourcePath: path });
        let output_written: string | null = null;
        if (typeof args["output"] === "string" && args["output"]) {
          const outPath = sanitizePath(args["output"] as string);
          try {
            writeCsv(outPath, result.goldenRecords);
            output_written = outPath;
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            return {
              run_id,
              stats: result.stats,
              total_clusters: result.stats.totalClusters,
              total_records: result.stats.totalRecords,
              match_rate: result.stats.matchRate,
              output_error: msg,
            };
          }
        }
        return {
          run_id,
          total_records: result.stats.totalRecords,
          total_clusters: result.stats.totalClusters,
          match_rate: result.stats.matchRate,
          matched_records: result.stats.matchedRecords,
          unique_records: result.stats.uniqueRecords,
          golden_records_count: result.goldenRecords.length,
          output_written,
        };
      }

      case "match_record":      // alias
      case "match": {
        const targetPath = sanitizePath(String(args["target"]));
        const referencePath = sanitizePath(String(args["reference"]));
        const targetRows = readFile(targetPath);
        const referenceRows = readFile(referencePath);
        const options = buildDedupeOptions(args);
        const result = await match(
          targetRows.map((r) => ({ ...r, __source__: "target" })),
          referenceRows.map((r) => ({ ...r, __source__: "reference" })),
          options,
        );
        let output_written: string | null = null;
        if (typeof args["output"] === "string" && args["output"]) {
          const outPath = sanitizePath(args["output"] as string);
          try {
            writeCsv(outPath, result.matched);
            output_written = outPath;
          } catch (err) {
            return {
              matched: result.matched.length,
              unmatched: result.unmatched.length,
              output_error: err instanceof Error ? err.message : String(err),
            };
          }
        }
        return {
          matched: result.matched.length,
          unmatched: result.unmatched.length,
          output_written,
        };
      }

      case "score_strings": {
        const a = String(args["a"] ?? "");
        const b = String(args["b"] ?? "");
        const scorer =
          typeof args["scorer"] === "string" ? (args["scorer"] as string) : "jaro_winkler";
        const score = scoreStrings(a, b, scorer);
        return { scorer, score };
      }

      case "score_pair": {
        const rowA = args["row_a"] as Row;
        const rowB = args["row_b"] as Row;
        if (!rowA || !rowB) throw new Error("row_a and row_b are required");
        const fields = buildFieldsFromArg(args["fields"]);
        const score = scorePair(rowA, rowB, fields);
        return { score, field_count: fields.length };
      }

      case "explain_match":     // alias
      case "explain_pair": {
        const rowA = args["row_a"] as Row;
        const rowB = args["row_b"] as Row;
        if (!rowA || !rowB) throw new Error("row_a and row_b are required");
        const fields = buildFieldsFromArg(args["fields"]);
        const threshold =
          typeof args["threshold"] === "number" ? (args["threshold"] as number) : 0.85;
        const mk = makeMatchkeyConfig({
          name: "adhoc",
          type: "weighted",
          fields,
          threshold,
        });
        const explanation = explainPair(rowA, rowB, mk);
        return {
          score: explanation.score,
          confidence: explanation.confidence,
          explanation: explanation.explanation,
          field_scores: explanation.fieldScores,
        };
      }

      case "explain_cluster": {
        const path = sanitizePath(String(args["path"]));
        const rowId = Number(args["row_id"]);
        if (!Number.isFinite(rowId)) {
          throw new Error("row_id must be a number");
        }
        const rows = readFile(path);
        const options = buildDedupeOptions(args);
        const result = await dedupe(rows, options);
        // Find cluster containing rowId
        let foundId: number | null = null;
        let found: typeof result.clusters extends ReadonlyMap<number, infer V> ? V : never;
        found = undefined as unknown as typeof found;
        for (const [cid, info] of result.clusters.entries()) {
          if (info.members.includes(rowId)) {
            foundId = cid;
            found = info;
            break;
          }
        }
        if (foundId === null || !found) {
          return { error: `row_id ${rowId} not found in any cluster` };
        }
        // Get matchkey
        const mks = (result.config.matchkeys ?? []) as readonly ReturnType<
          typeof makeMatchkeyConfig
        >[];
        const mk =
          mks.length > 0
            ? mks[0]!
            : makeMatchkeyConfig({
                name: "placeholder",
                type: "weighted",
                fields: [
                  makeMatchkeyField({
                    field: Object.keys(rows[0] ?? {})[0] ?? "",
                    transforms: ["lowercase", "strip"],
                    scorer: "jaro_winkler",
                  }),
                ],
              });
        const withIds = addRowIds(rows);
        const explanation = explainCluster(foundId, found, withIds, mk);
        return {
          cluster_id: explanation.clusterId,
          size: explanation.size,
          confidence: explanation.confidence,
          quality: explanation.quality,
          summary: explanation.summary,
        };
      }

      case "profile_data":      // alias
      case "profile": {
        const path = sanitizePath(String(args["path"]));
        const rows = readFile(path);
        const profile = profileRows(rows);
        return {
          row_count: profile.rowCount,
          columns: profile.columns.map((c) => ({
            name: c.name,
            inferred_type: c.inferredType,
            null_count: c.nullCount,
            null_rate: c.nullRate,
            distinct_count: c.distinctCount,
            cardinality_ratio: c.cardinalityRatio,
            avg_length: c.avgLength,
            max_length: c.maxLength,
            sample_values: c.sampleValues,
          })),
        };
      }

      case "suggest_config": {
        const path = sanitizePath(String(args["path"]));
        const rows = readFile(path);
        const profile = profileRows(rows);
        const exact: string[] = [];
        const fuzzy: Record<string, number> = {};
        const blocking: string[] = [];

        for (const col of profile.columns) {
          if (col.nullRate > 0.2) continue;
          if (col.inferredType === "email") {
            if (col.cardinalityRatio >= 0.5) exact.push(col.name);
          } else if (col.inferredType === "zip") {
            blocking.push(col.name);
          } else if (col.inferredType === "name") {
            fuzzy[col.name] = 0.85;
          } else if (col.inferredType === "phone") {
            if (col.cardinalityRatio >= 0.5) exact.push(col.name);
          } else if (col.inferredType === "geo") {
            blocking.push(col.name);
          } else if (
            (col.inferredType === "string" ||
              col.inferredType === "address" ||
              col.inferredType === "description") &&
            col.avgLength > 4
          ) {
            fuzzy[col.name] = 0.8;
          }
        }

        return {
          row_count: profile.rowCount,
          suggested: {
            exact,
            fuzzy,
            blocking,
            threshold: 0.85,
          },
        };
      }

      case "evaluate": {
        const path = sanitizePath(String(args["path"]));
        const gtPath = sanitizePath(String(args["ground_truth"]));
        const idColA =
          typeof args["id_col_a"] === "string" ? (args["id_col_a"] as string) : "id_a";
        const idColB =
          typeof args["id_col_b"] === "string" ? (args["id_col_b"] as string) : "id_b";
        const rows = readFile(path);
        const gtRows = readFile(gtPath);
        const options = buildDedupeOptions(args);
        const result = await dedupe(rows, options);
        const truth = loadGroundTruthPairs(gtRows, idColA, idColB);
        const metrics = evaluatePairs(result.scoredPairs, truth);
        return {
          tp: metrics.truePositives,
          fp: metrics.falsePositives,
          fn: metrics.falseNegatives,
          precision: metrics.precision,
          recall: metrics.recall,
          f1: metrics.f1,
          total_predicted: result.scoredPairs.length,
          total_truth: truth.length,
        };
      }

      case "find_exact_matches": {
        const path = sanitizePath(String(args["path"]));
        const field = String(args["field"]);
        const transforms = asStringArray(args["transforms"]) ?? ["lowercase", "strip"];
        const rows = addRowIds(readFile(path));
        const mk = makeMatchkeyConfig({
          name: "adhoc_exact",
          type: "exact",
          fields: [makeMatchkeyField({ field, transforms, scorer: "exact" })],
        });
        const pairs = findExactMatches(rows, mk);
        return {
          pair_count: pairs.length,
          pairs: pairs.slice(0, 100).map((p) => [p.idA, p.idB, p.score]),
        };
      }

      case "find_fuzzy_matches": {
        const path = sanitizePath(String(args["path"]));
        const field = String(args["field"]);
        const scorer =
          typeof args["scorer"] === "string" ? (args["scorer"] as string) : "jaro_winkler";
        const threshold =
          typeof args["threshold"] === "number" ? (args["threshold"] as number) : 0.85;
        const transforms = asStringArray(args["transforms"]) ?? ["lowercase", "strip"];
        const rows = addRowIds(readFile(path));
        const mk = makeMatchkeyConfig({
          name: "adhoc_fuzzy",
          type: "weighted",
          fields: [makeMatchkeyField({ field, transforms, scorer })],
          threshold,
        });
        const pairs = findFuzzyMatches(rows, mk);
        return {
          pair_count: pairs.length,
          pairs: pairs.slice(0, 100).map((p) => [p.idA, p.idB, p.score]),
        };
      }

      case "build_clusters": {
        const path = sanitizePath(String(args["path"]));
        const options = buildDedupeOptions(args);
        const rows = readFile(path);
        const result = await dedupe(rows, options);
        const clusters: Array<{
          cluster_id: number;
          size: number;
          confidence: number;
          quality: string;
          members: readonly number[];
        }> = [];
        for (const [cid, info] of result.clusters.entries()) {
          clusters.push({
            cluster_id: cid,
            size: info.size,
            confidence: info.confidence,
            quality: info.clusterQuality,
            members: info.members,
          });
        }
        return {
          cluster_count: clusters.length,
          clusters: clusters.slice(0, 200),
        };
      }

      case "list_scorers":
        return { scorers: [...VALID_SCORERS] };

      case "list_transforms":
        return { transforms: [...VALID_TRANSFORMS] };

      case "list_strategies":
        return { strategies: [...VALID_STRATEGIES] };

      case "list_blocking_strategies":
        return {
          strategies: [
            "static",
            "adaptive",
            "sorted_neighborhood",
            "multi_pass",
            "ann",
            "canopy",
            "ann_pairs",
            "learned",
          ],
        };

      case "server_info":
        return {
          name: "goldenmatch-js",
          version: "1.20.0",
          tool_count: TOOLS.length,
          description:
            "Node-only GoldenMatch MCP server over stdio (JSON-RPC 2.0)",
        };

      case "read_file": {
        const path = sanitizePath(String(args["path"]));
        const limit =
          typeof args["limit"] === "number" ? Math.max(0, Math.floor(args["limit"] as number)) : 100;
        const rows = readFile(path);
        return {
          total: rows.length,
          returned: Math.min(rows.length, limit),
          rows: rows.slice(0, limit),
        };
      }

      case "write_csv": {
        const path = sanitizePath(String(args["path"]));
        const rowsArg = args["rows"];
        if (!Array.isArray(rowsArg)) {
          throw new Error("rows must be an array of objects");
        }
        writeCsv(path, rowsArg as Row[]);
        return { written: rowsArg.length, path };
      }

      case "compare_clusters": {
        const pathA = sanitizePath(String(args["clusters_a_path"]));
        const pathB = sanitizePath(String(args["clusters_b_path"]));
        const a = parseClustersJson(JSON.parse(readFileSync(pathA, "utf-8")));
        const b = parseClustersJson(JSON.parse(readFileSync(pathB, "utf-8")));
        return ccmsSummary(compareClusters(a, b));
      }

      case "schema_match": {
        const pathA = sanitizePath(String(args["file_a"]));
        const pathB = sanitizePath(String(args["file_b"]));
        const rowsA = readFile(pathA);
        const rowsB = readFile(pathB);
        const minScore =
          typeof args["min_score"] === "number" ? (args["min_score"] as number) : 0.5;
        return { mappings: autoMapColumns(rowsA, rowsB, minScore) };
      }

      case "pprl_link": {
        const rowsA = readFile(sanitizePath(String(args["file_a"])));
        const rowsB = readFile(sanitizePath(String(args["file_b"])));
        const fields = Array.isArray(args["fields"]) ? args["fields"].map(String) : [];
        if (fields.length === 0) return { error: "Missing required parameter: fields" };
        const threshold = typeof args["threshold"] === "number" ? args["threshold"] : 0.85;
        const levelRaw = String(args["security_level"] ?? "high");
        const level: PPRLConfig["securityLevel"] =
          levelRaw === "standard" || levelRaw === "paranoid" ? levelRaw : "high";
        // security_level -> (ngram, hashes, bloom size), mirroring Python _LEVELS.
        const LEVELS: Record<string, readonly [number, number, number]> = {
          standard: [2, 20, 512],
          high: [2, 30, 1024],
          paranoid: [3, 40, 2048],
        };
        const [ngram, hashes, size] = LEVELS[level]!;
        const config: PPRLConfig = {
          fields,
          threshold,
          securityLevel: level,
          protocol: "trusted_third_party",
          ngramSize: ngram,
          hashFunctions: hashes,
          bloomFilterSize: size,
        };
        const result = runPPRL(rowsA, rowsB, config);
        const clusters = result.clusters.slice(0, 20).map((members, i) => ({
          cluster_id: i,
          members: members.map((m) => ({ party: m.party, record_id: m.id })),
        }));
        return {
          clusters_found: result.clusters.length,
          match_pairs: result.matchCount,
          total_comparisons: result.totalComparisons,
          security_level: level,
          threshold,
          fields,
          clusters,
        };
      }

      case "config_weaknesses": {
        const run = RUN_STORE.getCurrent();
        if (run === null) return { error: "No dataset loaded" };
        const maxFindings =
          typeof args["max_findings"] === "number"
            ? Math.floor(args["max_findings"] as number)
            : 6;
        const phrasing = args["phrasing"] === "technical" ? "technical" : "plain";
        const rows = [...run.rowsById.values()];
        return diagnoseConfig(rows, run.result.config, run.result, {
          maxFindings,
          phrasing,
        });
      }

      case "analyze_blocking": {
        const run = RUN_STORE.getCurrent();
        if (run === null) return { error: "No dataset loaded" };
        const matchkeys = getMatchkeys(run.result.config);
        const colSet = new Set<string>();
        for (const mk of matchkeys) {
          for (const f of mk.fields) colSet.add(f.field);
        }
        const cols = [...colSet].sort();
        const sampleSize =
          typeof args["sample_size"] === "number"
            ? Math.floor(args["sample_size"] as number)
            : 1000;
        const targetBlockSize =
          typeof args["target_block_size"] === "number"
            ? Math.floor(args["target_block_size"] as number)
            : 5000;
        const limit =
          typeof args["limit"] === "number" ? Math.floor(args["limit"] as number) : 10;
        const rows = [...run.rowsById.values()];
        const suggestions = analyzeBlocking(rows, cols, sampleSize, targetBlockSize);
        return { matchkey_columns: cols, suggestions: suggestions.slice(0, limit) };
      }

      case "certify_recall": {
        const filePath = String(args["file_path"] ?? "");
        let path: string;
        try {
          path = sanitizePath(filePath);
        } catch {
          return { error: `File not found: ${filePath}` };
        }
        let rows: readonly Row[];
        try {
          rows = readFile(path);
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          if (/ENOENT|not found|no such file/i.test(msg)) {
            return { error: `File not found: ${filePath}` };
          }
          return { error: `Could not read '${filePath}': ${msg}` };
        }
        const est = await certifyRecallRows(rows);
        return toCertifyRecallResponse(est);
      }

      case "sensitivity": {
        const filePath = String(args["file_path"] ?? "");
        let rows: readonly Row[];
        try {
          rows = readFile(sanitizePath(filePath));
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          if (/ENOENT|not found|no such file/i.test(msg)) {
            return { error: `File not found: ${filePath}` };
          }
          return { error: `Could not read '${filePath}': ${msg}` };
        }

        const cfgPath = args["config"];
        const cfg =
          typeof cfgPath === "string" && cfgPath.length > 0
            ? loadConfigFile(sanitizePath(cfgPath))
            : autoConfigureRows(rows);

        const sweepSpecs = asStringArray(args["sweep"]) ?? [];
        const sweeps: SweepSpec[] = [];
        for (const spec of sweepSpecs) {
          const parts = String(spec).split(":");
          if (parts.length !== 4) {
            return {
              error: `Bad sweep spec '${spec}'; expected 'field:start:stop:step'`,
            };
          }
          sweeps.push({
            field: parts[0]!,
            start: Number(parts[1]),
            stop: Number(parts[2]),
            step: Number(parts[3]),
          });
        }
        if (sweeps.length === 0) {
          return {
            error: "Provide at least one sweep spec, e.g. 'threshold:0.70:0.95:0.05'",
          };
        }

        const sampleSize =
          typeof args["sample_size"] === "number"
            ? Math.floor(args["sample_size"] as number)
            : undefined;
        const results = await runSensitivitySweep(rows, cfg, sweeps, sampleSize);
        return { results: results.map(sweepStabilityReport) };
      }

      case "incremental": {
        const baseFile = String(args["base_file"] ?? "");
        const newFile = String(args["new_records"] ?? "");
        let baseRows: readonly Row[];
        let newRows: readonly Row[];
        try {
          baseRows = readFile(sanitizePath(baseFile));
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          if (/ENOENT|not found|no such file/i.test(msg)) {
            return { error: `File not found: ${baseFile}` };
          }
          return { error: `Could not read '${baseFile}': ${msg}` };
        }
        try {
          newRows = readFile(sanitizePath(newFile));
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          if (/ENOENT|not found|no such file/i.test(msg)) {
            return { error: `File not found: ${newFile}` };
          }
          return { error: `Could not read '${newFile}': ${msg}` };
        }

        const cfgPath = args["config"];
        const cfg =
          typeof cfgPath === "string" && cfgPath.length > 0
            ? loadConfigFile(sanitizePath(cfgPath))
            : autoConfigureRows(baseRows);

        const threshold =
          typeof args["threshold"] === "number"
            ? (args["threshold"] as number)
            : undefined;
        return runIncremental(baseRows, newRows, cfg, threshold);
      }

      case "retrieve_similar": {
        const filePath = String(args["file_path"] ?? "");
        if (!filePath) return { error: "Missing required parameter: file_path" };
        const query = String(args["query"] ?? "");
        if (!query) return { error: "Missing required parameter: query" };
        const column = String(args["column"] ?? "");
        if (!column) return { error: "Missing required parameter: column" };

        // Caller-supplied embedder: the TS surface has NO bundled model (the
        // Python "inhouse" default has no TS equivalent), so require an explicit
        // provider and error clearly otherwise.
        const provider = args["provider"];
        if (
          provider !== "openai" &&
          provider !== "vertex" &&
          provider !== "voyage"
        ) {
          return {
            error:
              "retrieve_similar requires an embedder 'provider' " +
              "(openai/vertex/voyage): the TS surface carries only the " +
              "embedding kernel, not a bundled model. Pass provider + " +
              "credentials (api_key or the provider's env var).",
          };
        }

        let rows: readonly Row[];
        try {
          rows = readFile(sanitizePath(filePath));
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          if (/ENOENT|not found|no such file/i.test(msg)) {
            return { error: `File not found: ${filePath}` };
          }
          return { error: `Could not read '${filePath}': ${msg}` };
        }
        if (rows.length > 0 && !Object.prototype.hasOwnProperty.call(rows[0], column)) {
          return {
            error: `Column '${column}' not in ${filePath} (have ${Object.keys(
              rows[0] ?? {},
            ).join(", ")})`,
          };
        }

        const embedder = getEmbedder({
          provider,
          ...(typeof args["model"] === "string" && args["model"]
            ? { model: args["model"] as string }
            : {}),
          ...(typeof args["api_key"] === "string" && args["api_key"]
            ? { apiKey: args["api_key"] as string }
            : {}),
        });

        const k =
          typeof args["k"] === "number" ? Math.floor(args["k"] as number) : 20;
        const threshold =
          typeof args["threshold"] === "number" ? (args["threshold"] as number) : 0.0;
        const filters =
          args["filters"] && typeof args["filters"] === "object" && !Array.isArray(args["filters"])
            ? (args["filters"] as Record<string, unknown>)
            : null;

        const results = await retrieveSimilar(rows, query, column, {
          k,
          threshold,
          filters,
          embedder,
        });
        return {
          file: filePath,
          query,
          column,
          count: results.length,
          results: results.map(retrievedRecordToDict),
        };
      }

      case "convert_splink_config": {
        const settingsJson = args["settings_json"];
        const strict = args["strict"] === true;

        let settings: unknown;
        try {
          settings = JSON.parse(String(settingsJson));
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          return { error: `settings_json is not valid JSON: ${msg}` };
        }

        if (typeof settings !== "object" || settings === null || Array.isArray(settings)) {
          const got = settings === null ? "null" : Array.isArray(settings) ? "array" : typeof settings;
          return {
            error:
              "settings_json must decode to a JSON object (Splink settings dict), " +
              `got ${got}`,
          };
        }

        let conversion;
        try {
          conversion = fromSplink(settings, { strict });
        } catch (err) {
          if (err instanceof SplinkConversionError) {
            return { error: err.message };
          }
          throw err;
        }

        const configYaml = stringifyConfigYaml(conversion.config);
        const findings = conversion.report.findings.map((f) => ({
          severity: f.severity,
          splink_path: f.splinkPath,
          message: f.message,
          mapped_to: f.mappedTo,
        }));
        const emModel = conversion.emModel !== null ? emResultToJson(conversion.emModel) : null;
        const usageNote =
          "Save config_yaml to a file and load it as the GoldenMatch config. " +
          (emModel !== null
            ? "This model carries trained m/u probabilities: save em_model as JSON " +
              "and set matchkeys[0].model_path to that file's path so GoldenMatch " +
              "reuses it instead of re-training via EM."
            : "No trained model was carried by this input; GoldenMatch will train " +
              "via EM on first run.");

        return {
          config_yaml: configYaml,
          findings,
          summary: conversion.report.summary(),
          em_model: emModel,
          usage_note: usageNote,
        };
      }

      default:
        return { error: `Unknown tool: ${name}` };
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { error: msg };
  }
}

// ---------------------------------------------------------------------------
// JSON-RPC over stdio
// ---------------------------------------------------------------------------

interface JsonRpcRequest {
  jsonrpc?: string;
  id?: number | string | null;
  method?: string;
  params?: Record<string, unknown>;
}

function writeMessage(msg: Record<string, unknown>): void {
  process.stdout.write(JSON.stringify(msg) + "\n");
}

/**
 * Start the MCP server reading JSON-RPC messages one per line from stdin
 * and writing responses to stdout. Intended for Claude Desktop / any MCP
 * client using stdio transport.
 *
 * Unknown methods return a JSON-RPC error. Bad JSON is logged to stderr
 * (via console.warn) but does not crash the loop.
 */
export function startMcpServer(): void {
  const rl = createInterface({ input: process.stdin, terminal: false });

  rl.on("line", (line: string) => {
    if (line.trim() === "") return;
    let req: JsonRpcRequest;
    try {
      req = JSON.parse(line) as JsonRpcRequest;
    } catch (err) {
      console.warn(
        "MCP parse error:",
        err instanceof Error ? err.message : String(err),
      );
      return;
    }

    const id = req.id ?? null;

    void (async () => {
      try {
        if (req.method === "initialize") {
          writeMessage({
            jsonrpc: "2.0",
            id,
            result: {
              protocolVersion: "2024-11-05",
              serverInfo: { name: "goldenmatch-js", version: "1.20.0" },
              capabilities: { tools: {} },
            },
          });
          return;
        }

        if (req.method === "tools/list") {
          writeMessage({
            jsonrpc: "2.0",
            id,
            result: { tools: TOOLS },
          });
          return;
        }

        if (req.method === "tools/call") {
          const params = req.params ?? {};
          const toolName = String(params["name"] ?? "");
          const toolArgs =
            (params["arguments"] as Record<string, unknown> | undefined) ?? {};
          const result = await handleTool(toolName, toolArgs);
          writeMessage({
            jsonrpc: "2.0",
            id,
            result: {
              content: [
                { type: "text", text: JSON.stringify(result) },
              ],
            },
          });
          return;
        }

        if (
          req.method === "notifications/initialized" ||
          req.method === "notifications/cancelled"
        ) {
          // No response to notifications.
          return;
        }

        writeMessage({
          jsonrpc: "2.0",
          id,
          error: { code: -32601, message: `Method not found: ${req.method}` },
        });
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        writeMessage({
          jsonrpc: "2.0",
          id,
          error: { code: -32603, message: msg },
        });
      }
    })();
  });

  rl.on("close", () => {
    // Clean exit when stdin closes.
    process.exit(0);
  });
}

// Re-export for callers that want to pre-warm / test
export { readFileSync, isAbsolute };
export { writeJson };

// Run as a bin when invoked directly (the `goldenmatch-mcp` entry point).
// tsup compiles this to dist/node/mcp/server.{js,cjs}; the cjs build is the bin.
const isMain = (() => {
  try {
    return typeof require !== "undefined" && require.main === module;
  } catch {
    return false;
  }
})();

if (isMain) {
  startMcpServer();
}
