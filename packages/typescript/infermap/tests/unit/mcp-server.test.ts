// InferMap MCP server tests. Mirrors the goldenmatch MCP server test layout
// (TOOLS metadata + handleTool dispatcher) and adds resources/prompts coverage
// matching the Python sibling's surface (map/inspect/validate/apply tools,
// Supported Domains / Scorer Pipeline / Last Mapping Report resources,
// map-walkthrough / compare-schemas / domain-mapping prompts).

import { describe, it, expect, beforeAll, afterAll, beforeEach } from "vitest";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { readFile } from "node:fs/promises";
import { relative } from "node:path";

import {
  TOOLS,
  PROMPTS,
  handleTool,
  listResources,
  readResource,
  getPrompt,
  resetLastMappingReport,
} from "../../src/node/mcp/server.js";

describe("MCP server — TOOLS metadata", () => {
  it("exports the four InferMap tools", () => {
    const names = TOOLS.map((t) => t.name);
    expect(names).toEqual(["map", "inspect", "validate", "apply"]);
  });

  it("each tool has name, description, inputSchema", () => {
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

  it("map requires source + target; apply requires source/config/output", () => {
    const map = TOOLS.find((t) => t.name === "map")!;
    expect(map.inputSchema["required"]).toEqual(["source", "target"]);
    const apply = TOOLS.find((t) => t.name === "apply")!;
    expect(apply.inputSchema["required"]).toEqual(["source", "config", "output"]);
  });
});

describe("MCP server — handleTool dispatcher", () => {
  // sanitizePath restricts file args to within process.cwd(); create the temp
  // dir under cwd so the paths pass the guard. Use a relative path in args.
  let absDir: string;
  let dir: string; // relative to cwd

  beforeAll(async () => {
    absDir = await mkdtemp("./infermap-mcp-test-");
    dir = relative(process.cwd(), absDir);
  });
  afterAll(async () => {
    await rm(absDir, { recursive: true, force: true });
  });
  beforeEach(() => {
    resetLastMappingReport();
  });

  it("unknown tool returns { error } rather than throwing", async () => {
    const result = (await handleTool("nope", {})) as { error: string };
    expect(typeof result.error).toBe("string");
    expect(result.error).toMatch(/unknown/i);
  });

  it("inspect returns field-level stats for a CSV source", async () => {
    const p = `${dir}/people.csv`;
    await writeFile(p, "id,full_name,email\n1,Alice,a@x.com\n2,Bob,b@y.com\n");
    const result = (await handleTool("inspect", { source: p })) as {
      source_name: string;
      field_count: number;
      fields: Array<{ name: string; dtype: string; sample_values: string[] }>;
    };
    expect(result.field_count).toBe(3);
    expect(result.fields.map((f) => f.name)).toEqual(["id", "full_name", "email"]);
    expect(result.fields[0]!.dtype).toBe("integer");
    expect(result.fields[1]!.sample_values).toContain("Alice");
  });

  it("map produces mappings and caches the Last Mapping Report resource", async () => {
    const src = `${dir}/src.csv`;
    const tgt = `${dir}/tgt.csv`;
    await writeFile(src, "cust_id,full_name,email_addr\n1,Alice,a@x.com\n2,Bob,b@y.com\n");
    await writeFile(tgt, "customer_id,name,email\n9,Carol,c@z.com\n8,Dave,d@w.com\n");

    // Before any map: only 2 resources (no Last Mapping Report).
    expect(listResources().map((r) => r.uri)).toEqual([
      "infermap://supported-domains",
      "infermap://scorer-info",
    ]);

    const result = (await handleTool("map", {
      source: src,
      target: tgt,
    })) as { mappings: Array<{ source: string; target: string; confidence: number }> };
    expect(Array.isArray(result.mappings)).toBe(true);
    expect(result.mappings.length).toBeGreaterThan(0);

    // After map: Last Mapping Report resource is exposed and matches.
    const uris = listResources().map((r) => r.uri);
    expect(uris).toContain("infermap://last-mapping/report");
    const report = JSON.parse(readResource("infermap://last-mapping/report"));
    expect(report.mappings.length).toBe(result.mappings.length);
  });

  it("map honors min_confidence (high threshold drops weak mappings)", async () => {
    const src = `${dir}/src2.csv`;
    const tgt = `${dir}/tgt2.csv`;
    await writeFile(src, "a,b\nx,y\np,q\n");
    await writeFile(tgt, "zzz,qqq\n1,2\n3,4\n");
    const result = (await handleTool("map", {
      source: src,
      target: tgt,
      min_confidence: 0.99,
    })) as { mappings: unknown[] };
    expect(result.mappings.length).toBe(0);
  });

  it("validate reports missing sources and unmapped required fields", async () => {
    const src = `${dir}/vsrc.csv`;
    await writeFile(src, "cust_id,email_addr\n1,a@x.com\n");
    const cfg = `${dir}/cfg.json`;
    await writeFile(
      cfg,
      JSON.stringify({
        version: 1,
        mappings: [
          { source: "cust_id", target: "customer_id", confidence: 0.9 },
          { source: "missing_col", target: "phone", confidence: 0.9 },
        ],
      }),
    );
    const result = (await handleTool("validate", {
      source: src,
      config: cfg,
      required_fields: ["customer_id", "ssn"],
    })) as {
      all_sources_present: boolean;
      missing_sources: string[];
      required_fields_mapped: boolean;
      missing_required: string[];
      mapping_count: number;
    };
    expect(result.all_sources_present).toBe(false);
    expect(result.missing_sources).toEqual(["missing_col"]);
    expect(result.required_fields_mapped).toBe(false);
    expect(result.missing_required).toEqual(["ssn"]);
    expect(result.mapping_count).toBe(2);
  });

  it("apply renames columns and writes the output CSV", async () => {
    const src = `${dir}/asrc.csv`;
    await writeFile(src, "cust_id,email_addr\n1,a@x.com\n2,b@y.com\n");
    const cfg = `${dir}/acfg.json`;
    await writeFile(
      cfg,
      JSON.stringify({
        version: 1,
        mappings: [
          { source: "cust_id", target: "customer_id", confidence: 0.9 },
          { source: "email_addr", target: "email", confidence: 0.9 },
        ],
      }),
    );
    const out = `${dir}/out.csv`;
    const result = (await handleTool("apply", {
      source: src,
      config: cfg,
      output: out,
    })) as { rows_written: number; columns: string[] };
    expect(result.rows_written).toBe(2);
    expect(result.columns).toEqual(["customer_id", "email"]);
    const written = await readFile(out, "utf8");
    expect(written.split("\n")[0]).toBe("customer_id,email");
  });

  it("path traversal via '..' is rejected (error, not crash)", async () => {
    const result = (await handleTool("inspect", {
      source: "../../../etc/passwd",
    })) as { error?: string };
    expect(typeof result.error).toBe("string");
    expect(result.error).toMatch(/outside|enoent|no such/i);
  });

  it("absolute path outside cwd is rejected", async () => {
    const outside =
      process.platform === "win32"
        ? "C:\\Windows\\System32\\drivers\\etc\\hosts"
        : "/etc/passwd";
    const result = (await handleTool("inspect", { source: outside })) as {
      error?: string;
    };
    expect(typeof result.error).toBe("string");
    expect(result.error).toMatch(/outside|enoent|no such/i);
  });
});

describe("MCP server — resources", () => {
  beforeEach(() => resetLastMappingReport());

  it("lists Supported Domains + Scorer Pipeline by default", () => {
    const resources = listResources();
    const uris = resources.map((r) => r.uri);
    expect(uris).toContain("infermap://supported-domains");
    expect(uris).toContain("infermap://scorer-info");
    expect(uris).not.toContain("infermap://last-mapping/report");
  });

  it("Supported Domains resource returns known domains", () => {
    const data = JSON.parse(readResource("infermap://supported-domains"));
    expect(Array.isArray(data.domains)).toBe(true);
    expect(data.domains).toContain("generic");
    expect(data.domains).toContain("healthcare");
  });

  it("Scorer Pipeline resource returns scorers with names + weights", () => {
    const data = JSON.parse(readResource("infermap://scorer-info"));
    expect(Array.isArray(data.scorers)).toBe(true);
    expect(data.scorers.length).toBeGreaterThan(0);
    for (const s of data.scorers) {
      expect(typeof s.name).toBe("string");
      expect(typeof s.weight).toBe("number");
    }
  });

  it("Last Mapping Report errors gracefully before any map", () => {
    const data = JSON.parse(readResource("infermap://last-mapping/report"));
    expect(data.error).toMatch(/no mapping/i);
  });

  it("unknown resource URI returns an error object", () => {
    const data = JSON.parse(readResource("infermap://does-not-exist"));
    expect(data.error).toMatch(/unknown resource/i);
  });
});

describe("MCP server — prompts", () => {
  it("exposes the three guided prompts", () => {
    expect(PROMPTS.map((p) => p.name)).toEqual([
      "map-walkthrough",
      "compare-schemas",
      "domain-mapping",
    ]);
    for (const p of PROMPTS) {
      expect(p.description.length).toBeGreaterThan(0);
      expect(Array.isArray(p.arguments)).toBe(true);
    }
  });

  it("map-walkthrough interpolates source + target", () => {
    const msgs = getPrompt("map-walkthrough", { source: "a.csv", target: "b.csv" });
    expect(msgs.length).toBe(1);
    expect(msgs[0]!.role).toBe("user");
    expect(msgs[0]!.content.text).toContain("a.csv");
    expect(msgs[0]!.content.text).toContain("b.csv");
  });

  it("domain-mapping defaults the domain to 'generic' when absent", () => {
    const msgs = getPrompt("domain-mapping", { source: "s", target: "t" });
    expect(msgs[0]!.content.text).toContain("'generic'");
  });

  it("compare-schemas uses source_a / source_b", () => {
    const msgs = getPrompt("compare-schemas", { source_a: "x", source_b: "y" });
    expect(msgs[0]!.content.text).toContain("'x'");
    expect(msgs[0]!.content.text).toContain("'y'");
  });

  it("unknown prompt returns an 'Unknown prompt' message", () => {
    const msgs = getPrompt("nope", {});
    expect(msgs[0]!.content.text).toMatch(/unknown prompt/i);
  });
});
