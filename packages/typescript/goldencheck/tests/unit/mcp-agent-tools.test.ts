/**
 * MCP agent-tools tests. Parity with the Python
 * tests/test_mcp_agent_tools.py surface (analyze/auto_configure/explain/
 * review/compare/fix/handoff). File-based tools run against the committed
 * tests/fixtures/simple.csv.
 */

import { describe, it, expect, beforeEach } from "vitest";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

import {
  AGENT_TOOLS,
  AGENT_TOOL_NAMES,
  handleAgentTool,
  __resetReviewQueueForTests,
} from "../../src/node/mcp/agent-tools.js";
import { TOOL_DEFINITIONS, handleTool } from "../../src/node/mcp/server.js";

const __dirnameLocal = dirname(fileURLToPath(import.meta.url));
const SIMPLE_CSV = join(__dirnameLocal, "..", "fixtures", "simple.csv");

beforeEach(() => __resetReviewQueueForTests());

describe("AGENT_TOOLS metadata", () => {
  it("exports the 10 agent tools matching the Python sibling", () => {
    expect(AGENT_TOOLS.map((t) => t.name)).toEqual([
      "analyze_data",
      "auto_configure",
      "explain_finding",
      "explain_column",
      "review_queue",
      "approve_reject",
      "compare_domains",
      "suggest_fix",
      "pipeline_handoff",
      "review_stats",
    ]);
    expect(AGENT_TOOL_NAMES.size).toBe(10);
  });

  it("are merged into the server's TOOL_DEFINITIONS (8 core + 10 agent = 18)", () => {
    expect(TOOL_DEFINITIONS.length).toBe(18);
    expect(TOOL_DEFINITIONS.map((t) => t.name)).toContain("analyze_data");
    expect(TOOL_DEFINITIONS.map((t) => t.name)).toContain("scan");
    expect(TOOL_DEFINITIONS.map((t) => t.name)).toContain("validate");
  });
});

describe("agent tool dispatch (file-based)", () => {
  it("analyze_data returns strategy + counts", () => {
    const r = handleAgentTool("analyze_data", { file_path: SIMPLE_CSV }) as Record<string, unknown>;
    expect(r["rows"]).toBeGreaterThan(0);
    expect(Array.isArray(r["column_names"])).toBe(true);
    expect((r["strategy"] as Record<string, unknown>)["profiler_strategy"]).toBeTypeOf("string");
  });

  it("auto_configure returns rules + goldencheck.yml content", () => {
    const r = handleAgentTool("auto_configure", { file_path: SIMPLE_CSV }) as Record<string, unknown>;
    expect(Array.isArray(r["rules"])).toBe(true);
    expect(r["yaml_content"]).toBeTypeOf("string");
    expect(String(r["yaml_content"])).toContain("version: 1");
    expect(r["pinned_count"]).toBeTypeOf("number");
  });

  it("auto_configure honors exclude_columns constraint", () => {
    const r = handleAgentTool("auto_configure", {
      file_path: SIMPLE_CSV,
      constraints: { exclude_columns: ["email", "name", "age", "status", "id"] },
    }) as Record<string, unknown>;
    expect(r["pinned_count"]).toBe(0);
  });

  it("explain_column returns a narrative object", () => {
    const r = handleAgentTool("explain_column", { file_path: SIMPLE_CSV, column: "email" }) as Record<string, unknown>;
    expect(typeof r).toBe("object");
    expect(Object.keys(r).length).toBeGreaterThan(0);
  });

  it("compare_domains returns a comparison object", () => {
    const r = handleAgentTool("compare_domains", { file_path: SIMPLE_CSV }) as Record<string, unknown>;
    expect(typeof r).toBe("object");
  });

  it("suggest_fix previews fixes without applying", () => {
    const r = handleAgentTool("suggest_fix", { file_path: SIMPLE_CSV }) as Record<string, unknown>;
    expect(r["mode"]).toBe("safe");
    expect(Array.isArray(r["fixes"])).toBe(true);
    expect(r["total_rows_fixed"]).toBeTypeOf("number");
  });

  it("pipeline_handoff returns a quality attestation", () => {
    const r = handleAgentTool("pipeline_handoff", { file_path: SIMPLE_CSV, job_name: "job1" }) as Record<string, unknown>;
    expect(r["source_tool"]).toBe("goldencheck");
    expect(r["job_name"]).toBe("job1");
    expect(r["health"]).toBeTypeOf("object");
  });

  it("returns File not found for a missing path", () => {
    const r = handleAgentTool("analyze_data", { file_path: "does-not-exist.csv" }) as Record<string, unknown>;
    expect(String(r["error"])).toContain("File not found");
  });
});

describe("agent tool dispatch (review queue)", () => {
  it("review_queue is empty for a fresh job", () => {
    const r = handleAgentTool("review_queue", { job_name: "job1" }) as Record<string, unknown>;
    expect(r["pending_count"]).toBe(0);
    expect(r["items"]).toEqual([]);
  });

  it("review_stats returns zeroed counts for a fresh job", () => {
    const r = handleAgentTool("review_stats", { job_name: "job1" }) as Record<string, unknown>;
    expect(r["pending"]).toBe(0);
    expect(r["approved"]).toBe(0);
    expect(r["rejected"]).toBe(0);
  });

  it("approve_reject errors on an unknown item", () => {
    const r = handleAgentTool("approve_reject", { item_id: "nope", decision: "pin" }) as Record<string, unknown>;
    expect(String(r["error"])).toContain("not found");
  });

  it("approve_reject validates the decision value", () => {
    const r = handleAgentTool("approve_reject", { item_id: "x", decision: "maybe" }) as Record<string, unknown>;
    expect(String(r["error"])).toContain("pin");
  });
});

describe("server routes agent tools through handleTool", () => {
  it("handleTool dispatches review_stats to the agent handler", () => {
    const r = handleTool("review_stats", { job_name: "jobX" }) as Record<string, unknown>;
    expect(r["job_name"]).toBe("jobX");
    expect(r["pending"]).toBe(0);
  });
});
