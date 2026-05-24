// GoldenPipe MCP server tests. Mirrors the sibling InferMap / GoldenMatch MCP
// server test layout (TOOLS metadata + handleTool dispatcher) and matches the
// Python sibling's 4-tool surface: list_stages, validate_pipeline, run_pipeline,
// explain_pipeline.

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { relative, join } from "node:path";

import { TOOLS, handleTool } from "../../src/node/mcp/server.js";

describe("MCP server — TOOLS metadata", () => {
  it("exports the four GoldenPipe tools", () => {
    const names = TOOLS.map((t) => t.name);
    expect(names).toEqual([
      "list_stages",
      "validate_pipeline",
      "run_pipeline",
      "explain_pipeline",
    ]);
  });

  it("each tool has a name, description, and inputSchema", () => {
    for (const tool of TOOLS) {
      expect(typeof tool.name).toBe("string");
      expect(tool.name.length).toBeGreaterThan(0);
      expect(typeof tool.description).toBe("string");
      expect(tool.description.length).toBeGreaterThan(0);
      expect(tool.inputSchema).toBeTypeOf("object");
      expect(tool.inputSchema).not.toBeNull();
    }
  });

  it("every tool name is unique", () => {
    const names = TOOLS.map((t) => t.name);
    expect(new Set(names).size).toBe(names.length);
  });

  it("declares required args matching the Python sibling", () => {
    const validate = TOOLS.find((t) => t.name === "validate_pipeline")!;
    expect(validate.inputSchema["required"]).toEqual(["pipeline", "stages"]);
    const run = TOOLS.find((t) => t.name === "run_pipeline")!;
    expect(run.inputSchema["required"]).toEqual(["source"]);
    const explain = TOOLS.find((t) => t.name === "explain_pipeline")!;
    expect(explain.inputSchema["required"]).toEqual(["config_path"]);
  });
});

describe("MCP server — handleTool dispatcher", () => {
  it("list_stages returns the built-in suite stages with contracts", async () => {
    const result = (await handleTool("list_stages", {})) as Record<
      string,
      { produces: string[]; consumes: string[] }
    >;
    expect(Object.keys(result).sort()).toEqual([
      "goldencheck.scan",
      "goldenflow.transform",
      "goldenmatch.dedupe",
      "load",
    ]);
    expect(result["load"]!.produces).toContain("df");
    expect(result["goldencheck.scan"]!.consumes).toContain("df");
  });

  it("validate_pipeline accepts a well-wired chain", async () => {
    const result = (await handleTool("validate_pipeline", {
      pipeline: "demo",
      stages: ["goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe"],
    })) as { valid: boolean; stages: string[] };
    expect(result.valid).toBe(true);
    // load is auto-prepended by the resolver.
    expect(result.stages).toEqual([
      "load",
      "goldencheck.scan",
      "goldenflow.transform",
      "goldenmatch.dedupe",
    ]);
  });

  it("validate_pipeline surfaces an error for an unknown stage", async () => {
    const result = (await handleTool("validate_pipeline", {
      pipeline: "demo",
      stages: ["does.not.exist"],
    })) as { error?: string; valid?: boolean };
    expect(result.error).toBeTypeOf("string");
  });

  it("run_pipeline maps PipeResult to snake_case keys (failed read path)", async () => {
    const result = (await handleTool("run_pipeline", {
      source: "definitely-missing-file.csv",
    })) as { status: string; input_rows: number; errors: string[] };
    expect(result.status).toBe("failed");
    expect(result.input_rows).toBe(0);
    expect(result.errors.length).toBeGreaterThan(0);
  });

  it("explain_pipeline resolves a YAML config into an ordered plan", async () => {
    const absDir = await mkdtemp("./goldenpipe-mcp-test-");
    const dir = relative(process.cwd(), absDir);
    const configPath = join(dir, "pipe.yml");
    await writeFile(
      configPath,
      "pipeline: demo\nstages:\n  - goldencheck.scan\n  - goldenflow.transform\n",
      "utf-8",
    );
    try {
      const result = (await handleTool("explain_pipeline", {
        config_path: configPath,
      })) as { pipeline: string; stages: { name: string }[] };
      expect(result.pipeline).toBe("demo");
      expect(result.stages.map((s) => s.name)).toEqual([
        "load",
        "goldencheck.scan",
        "goldenflow.transform",
      ]);
    } finally {
      await rm(absDir, { recursive: true, force: true });
    }
  });

  it("rejects file paths outside the working directory", async () => {
    const result = (await handleTool("run_pipeline", {
      source: "/etc/passwd",
    })) as { error?: string };
    expect(result.error).toBeTypeOf("string");
    expect(result.error).toContain("outside the working directory");
  });

  it("returns an error object for an unknown tool", async () => {
    const result = (await handleTool("nonexistent_tool", {})) as { error: string };
    expect(result.error).toContain("Unknown tool");
  });
});
