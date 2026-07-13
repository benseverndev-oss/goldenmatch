/**
 * mcp-convert-splink.test.ts -- Task T4: the `convert_splink_config` MCP tool.
 *
 * Mirrors the response shape assembled by the Python tool
 * (`_tool_convert_splink_config` in goldenmatch/mcp/server.py): settings
 * arrive as an inline JSON string (no filesystem access), and the tool
 * returns `config_yaml` / `findings` (snake_case) / `summary` / `em_model` /
 * `usage_note`.
 */
import { describe, it, expect } from "vitest";
import { TOOLS, handleTool } from "../../src/node/mcp/server.js";

function bareSettings(): Record<string, unknown> {
  return {
    comparisons: [
      {
        output_column_name: "first_name",
        comparison_levels: [
          { sql_condition: '"first_name_l" IS NULL OR "first_name_r" IS NULL', is_null_level: true },
          { sql_condition: '"first_name_l" = "first_name_r"' },
          { sql_condition: "ELSE" },
        ],
      },
    ],
    blocking_rules_to_generate_predictions: ['l."first_name" = r."first_name"'],
  };
}

function trainedSettings(): Record<string, unknown> {
  return {
    comparisons: [
      {
        output_column_name: "first_name",
        comparison_levels: [
          { sql_condition: '"first_name_l" IS NULL OR "first_name_r" IS NULL', is_null_level: true },
          { sql_condition: '"first_name_l" = "first_name_r"', m_probability: 0.8, u_probability: 0.02 },
          { sql_condition: "ELSE", m_probability: 0.2, u_probability: 0.98 },
        ],
      },
    ],
    blocking_rules_to_generate_predictions: ['l."first_name" = r."first_name"'],
    probability_two_random_records_match: 0.001,
  };
}

describe("MCP server — TOOLS includes convert_splink_config", () => {
  it("is registered with settings_json required", () => {
    const tool = TOOLS.find((t) => t.name === "convert_splink_config");
    expect(tool).toBeDefined();
    const schema = tool!.inputSchema as { required?: string[] };
    expect(schema.required).toContain("settings_json");
  });
});

describe("MCP server — convert_splink_config handler", () => {
  it("happy path (bare settings) returns the full response shape", async () => {
    const result = (await handleTool("convert_splink_config", {
      settings_json: JSON.stringify(bareSettings()),
    })) as Record<string, unknown>;

    expect(typeof result["config_yaml"]).toBe("string");
    expect((result["config_yaml"] as string)).toContain("matchkeys");
    expect(Array.isArray(result["findings"])).toBe(true);
    const findings = result["findings"] as Record<string, unknown>[];
    expect(findings.length).toBeGreaterThan(0);
    expect(findings[0]).toHaveProperty("severity");
    expect(findings[0]).toHaveProperty("splink_path");
    expect(findings[0]).toHaveProperty("message");
    expect(findings[0]).toHaveProperty("mapped_to");
    expect(typeof result["summary"]).toBe("string");
    expect(result["em_model"]).toBeNull();
    expect(typeof result["usage_note"]).toBe("string");
    expect(result["usage_note"] as string).toContain("No trained model");
  });

  it("trained settings return a non-null em_model dict", async () => {
    const result = (await handleTool("convert_splink_config", {
      settings_json: JSON.stringify(trainedSettings()),
    })) as Record<string, unknown>;

    expect(result["em_model"]).not.toBeNull();
    const em = result["em_model"] as Record<string, unknown>;
    expect(em["__type__"]).toBe("goldenmatch.EMResult");
    expect(em["match_weights"]).toBeDefined();
    expect(result["usage_note"] as string).toContain("trained m/u probabilities");
  });

  it("bad JSON returns the error convention", async () => {
    const result = (await handleTool("convert_splink_config", {
      settings_json: "{not valid json",
    })) as Record<string, unknown>;
    expect(typeof result["error"]).toBe("string");
    expect(result["error"] as string).toMatch(/not valid JSON/);
  });

  it("non-object JSON returns the error convention", async () => {
    const result = (await handleTool("convert_splink_config", {
      settings_json: JSON.stringify([1, 2, 3]),
    })) as Record<string, unknown>;
    expect(typeof result["error"]).toBe("string");
    expect(result["error"] as string).toMatch(/must decode to a JSON object/);
  });

  it("zero-convertible-comparisons returns the error convention", async () => {
    const result = (await handleTool("convert_splink_config", {
      settings_json: JSON.stringify({ comparisons: [] }),
    })) as Record<string, unknown>;
    expect(typeof result["error"]).toBe("string");
  });

  it("strict=true fails on lossy findings", async () => {
    const settings = bareSettings();
    (settings["comparisons"] as Record<string, unknown>[])[0]!["comparison_levels"] = [
      ...((settings["comparisons"] as Record<string, unknown>[])[0]!["comparison_levels"] as unknown[]),
      { sql_condition: "some_weird_condition(x, y)" },
    ];
    const result = (await handleTool("convert_splink_config", {
      settings_json: JSON.stringify(settings),
      strict: true,
    })) as Record<string, unknown>;
    expect(typeof result["error"]).toBe("string");
  });
});
