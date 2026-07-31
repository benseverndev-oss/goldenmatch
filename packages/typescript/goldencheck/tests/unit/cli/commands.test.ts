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

  it("registers the refs command (cross-file referential integrity)", () => {
    expect(names).toContain("refs");
    const refs = program.commands.find((c) => c.name() === "refs");
    expect(refs).toBeDefined();
    // Two positional args: <child> <parent>.
    expect(refs!.registeredArguments.map((a) => a.name())).toEqual(["child", "parent"]);
    // Repeatable --on mapping, --json, and --fail-on (default error).
    expect(refs!.options.some((o) => o.long === "--on")).toBe(true);
    const failOn = refs!.options.find((o) => o.long === "--fail-on");
    expect(failOn).toBeDefined();
    expect(failOn!.defaultValue).toBe("error");
  });

  it("registers the denial-constraints command (TS parity with the DC engine)", () => {
    expect(names).toContain("denial-constraints");
    const dc = program.commands.find((c) => c.name() === "denial-constraints");
    expect(dc).toBeDefined();
    // Single positional <file>.
    expect(dc!.registeredArguments.map((a) => a.name())).toEqual(["file"]);
    // The three engine knobs, mirroring goldencheck/cli/main.py.
    expect(dc!.options.some((o) => o.long === "--min-confidence")).toBe(true);
    expect(dc!.options.some((o) => o.long === "--sample-size")).toBe(true);
    expect(dc!.options.some((o) => o.long === "--max-constraints")).toBe(true);
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
