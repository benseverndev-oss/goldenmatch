import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { handleTool, TOOL_DEFINITIONS } from "../../src/node/mcp/server.js";

let dir: string;
let dataPath: string;
let configPath: string;

beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), "gc-validate-"));
  dataPath = join(dir, "data.csv");
  configPath = join(dir, "goldencheck.yml");
  // status has an out-of-enum value "bogus".
  writeFileSync(dataPath, "id,status\n1,active\n2,bogus\n3,active\n");
  writeFileSync(
    configPath,
    [
      "version: 1",
      "settings:",
      "  sample_size: 100000",
      "  severity_threshold: warning",
      "  fail_on: error",
      "columns:",
      "  status:",
      "    type: string",
      "    enum: [active, inactive]",
      "relations: []",
      "ignore: []",
      "",
    ].join("\n"),
  );
});

afterAll(() => rmSync(dir, { recursive: true, force: true }));

describe("validate MCP tool", () => {
  it("is registered (8 core + 10 agent = 18 tools)", () => {
    expect(TOOL_DEFINITIONS.length).toBe(18);
    expect(TOOL_DEFINITIONS.map((t) => t.name)).toContain("validate");
  });

  it("reports enum violations and fails the gate", () => {
    const r = handleTool("validate", {
      file_path: dataPath,
      config_path: configPath,
    }) as Record<string, unknown>;
    expect(r["pass"]).toBe(false);
    expect(r["errors"] as number).toBeGreaterThanOrEqual(1);
    const findings = r["findings"] as Array<{ check: string }>;
    expect(findings.some((f) => f.check === "enum")).toBe(true);
  });

  it("returns an error for a missing file", () => {
    const r = handleTool("validate", {
      file_path: join(dir, "nope.csv"),
      config_path: configPath,
    }) as Record<string, unknown>;
    expect(typeof r["error"]).toBe("string");
  });

  it("returns an error for a missing config", () => {
    const r = handleTool("validate", {
      file_path: dataPath,
      config_path: join(dir, "nope.yml"),
    }) as Record<string, unknown>;
    expect(typeof r["error"]).toBe("string");
  });
});
