import { describe, it, expect } from "vitest";
import { program } from "../../../src/cli.js";

// Importing cli.ts is side-effect-free: the `program.parse()` call at the bottom
// is guarded by an `import.meta.url === argv[1]` check, so it doesn't run here.

describe("goldencheck CLI command registration", () => {
  const names = program.commands.map((c) => c.name());

  it("registers the history + evaluate commands (TS parity with Python)", () => {
    expect(names).toContain("history");
    expect(names).toContain("evaluate");
  });

  it("evaluate requires a --ground-truth option", () => {
    const evaluate = program.commands.find((c) => c.name() === "evaluate");
    expect(evaluate).toBeDefined();
    const gt = evaluate!.options.find((o) => o.long === "--ground-truth");
    expect(gt).toBeDefined();
    expect(gt!.required).toBe(true);
    // --min-f1 gate is present for CI-style accuracy checks.
    expect(evaluate!.options.some((o) => o.long === "--min-f1")).toBe(true);
  });

  it("scan gained a --no-history opt-out (history recorded by default)", () => {
    const scan = program.commands.find((c) => c.name() === "scan");
    expect(scan).toBeDefined();
    expect(scan!.options.some((o) => o.long === "--no-history")).toBe(true);
  });

  it("history accepts an optional file filter + --last / --json", () => {
    const history = program.commands.find((c) => c.name() === "history");
    expect(history).toBeDefined();
    expect(history!.options.some((o) => o.long === "--last")).toBe(true);
    expect(history!.options.some((o) => o.long === "--json")).toBe(true);
  });

  it("registers the learn command (TS parity with Python's LLM rule generator)", () => {
    expect(names).toContain("learn");
    const learn = program.commands.find((c) => c.name() === "learn");
    expect(learn).toBeDefined();
    // -o/--output with the same default filename as the Python command.
    const output = learn!.options.find((o) => o.long === "--output");
    expect(output).toBeDefined();
    expect(output!.short).toBe("-o");
    expect(output!.defaultValue).toBe("goldencheck_rules.json");
    // --llm-provider defaults to anthropic (mirrors goldencheck/cli/main.py).
    const provider = learn!.options.find((o) => o.long === "--llm-provider");
    expect(provider).toBeDefined();
    expect(provider!.defaultValue).toBe("anthropic");
  });
});
