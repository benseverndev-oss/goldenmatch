/**
 * Unit tests for YAML config loading + normalization (node layer).
 */

import { describe, it, expect, afterAll } from "vitest";
import { writeFileSync, rmSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadConfig, normalizeConfig } from "../../src/node/index.js";

const dir = mkdtempSync(join(tmpdir(), "gp-cfg-"));
afterAll(() => rmSync(dir, { recursive: true, force: true }));

describe("normalizeConfig", () => {
  it("normalizes bare-string and object stages", () => {
    const cfg = normalizeConfig({
      pipeline: "p",
      stages: [
        "goldencheck.scan",
        { use: "goldenmatch.dedupe", on_error: "abort", skip_if: "df", config: { threshold: 0.9 } },
      ],
    });
    expect(cfg.pipeline).toBe("p");
    expect(cfg.stages).toHaveLength(2);
    const [a, b] = cfg.stages as [Exclude<(typeof cfg.stages)[number], string>, Exclude<(typeof cfg.stages)[number], string>];
    expect(a.use).toBe("goldencheck.scan");
    expect(a.onError).toBe("continue");
    expect(b.use).toBe("goldenmatch.dedupe");
    expect(b.onError).toBe("abort");
    expect(b.skipIf).toBe("df");
    expect(b.config).toEqual({ threshold: 0.9 });
  });

  it("rejects a non-list stages field", () => {
    expect(() => normalizeConfig({ pipeline: "p", stages: "nope" })).toThrow(/must be a list/);
  });

  it("rejects a stage object missing use/name", () => {
    expect(() => normalizeConfig({ pipeline: "p", stages: [{ foo: 1 }] })).toThrow(/'use' field/);
  });

  it("rejects a config with no pipeline name", () => {
    expect(() => normalizeConfig({ stages: [] })).toThrow(/pipeline/);
  });
});

describe("loadConfig", () => {
  it("loads + normalizes a YAML file", async () => {
    const p = join(dir, "pipe.yml");
    writeFileSync(
      p,
      ["pipeline: my-pipeline", "stages:", "  - goldencheck.scan", "  - goldenflow.transform"].join("\n"),
    );
    const cfg = await loadConfig(p);
    expect(cfg.pipeline).toBe("my-pipeline");
    expect(cfg.stages).toHaveLength(2);
  });

  it("throws on a missing file", async () => {
    await expect(loadConfig(join(dir, "nope.yml"))).rejects.toThrow(/not found/);
  });
});
