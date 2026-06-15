/**
 * mcp-agent-tools.test.ts -- AGENT_MCP_TOOLS surface + handleAgentTool dispatch.
 *
 * Asserts (a) the rendered tool surface (count, names, schemas) mirrors
 * AGENT_SKILLS, (b) the tool name set, and (c) an end-to-end dispatch through
 * the node SkillContext (analyze_data on inline rows returns a strategy), plus
 * the optional-dep fail-open path.
 */
import { describe, it, expect } from "vitest";
import {
  AGENT_MCP_TOOLS,
  AGENT_TOOL_NAMES,
  handleAgentTool,
} from "../../src/node/mcp/agent-tools.js";
import { AGENT_SKILLS } from "../../src/core/agent/skills.js";

describe("AGENT_MCP_TOOLS surface", () => {
  it("renders exactly 14 tools", () => {
    expect(AGENT_MCP_TOOLS.length).toBe(14);
  });

  it("mirrors AGENT_SKILLS (name/description/inputSchema)", () => {
    expect(AGENT_MCP_TOOLS.length).toBe(AGENT_SKILLS.length);
    for (let i = 0; i < AGENT_SKILLS.length; i++) {
      const skill = AGENT_SKILLS[i]!;
      const tool = AGENT_MCP_TOOLS[i]!;
      expect(tool.name).toBe(skill.id);
      expect(tool.description).toBe(skill.description);
      expect(tool.inputSchema).toBe(skill.inputSchema);
    }
  });

  it("each tool has a non-empty name, description, object schema", () => {
    for (const tool of AGENT_MCP_TOOLS) {
      expect(typeof tool.name).toBe("string");
      expect(tool.name.length).toBeGreaterThan(0);
      expect(typeof tool.description).toBe("string");
      expect(tool.description.length).toBeGreaterThan(0);
      expect(tool.inputSchema).toBeTypeOf("object");
      expect(tool.inputSchema).not.toBeNull();
    }
  });

  it("AGENT_TOOL_NAMES covers every rendered tool", () => {
    expect(AGENT_TOOL_NAMES.size).toBe(AGENT_MCP_TOOLS.length);
    for (const tool of AGENT_MCP_TOOLS) {
      expect(AGENT_TOOL_NAMES.has(tool.name)).toBe(true);
    }
  });
});

describe("handleAgentTool dispatch", () => {
  it("analyze_data on inline rows returns a strategy", async () => {
    const result = await handleAgentTool("analyze_data", {
      rows: [
        { id: "1", name: "Alice" },
        { id: "2", name: "Alyce" },
      ],
    });
    expect(result.strategy).toBeDefined();
  });

  it("run_transforms fails open when goldenflow is absent", async () => {
    const result = await handleAgentTool("run_transforms", {
      rows: [{ id: "1", name: "a" }],
    });
    expect(result.error).toMatch(/goldenflow not installed/);
  });

  it("unknown agent tool returns {error}", async () => {
    const result = await handleAgentTool("does_not_exist", {});
    expect(result.error).toMatch(/unknown skill/i);
  });
});
